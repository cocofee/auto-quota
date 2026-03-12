"""
统一Benchmark系统 — 一个命令跑全部试卷。

支持两种试卷格式：
  1. JSON固定试卷（tests/benchmark_papers/*.json）— 有标准答案，算命中率
  2. Excel数据集（tests/benchmark_config.json定义）— 算置信度绿/黄/红率

用法：
  python tools/run_benchmark.py                    # 跑全部试卷
  python tools/run_benchmark.py --save             # 跑完保存为基线
  python tools/run_benchmark.py --compare          # 与基线对比
  python tools/run_benchmark.py --province 广东    # 只跑含"广东"的省份
  python tools/run_benchmark.py --province 北京 --detail  # 打印每题详情
  python tools/run_benchmark.py --excel-only       # 只跑Excel数据集（老模式）
  python tools/run_benchmark.py --show-baseline    # 查看当前基线
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

# 路径配置
PROJECT_ROOT = Path(__file__).parent.parent
PAPERS_DIR = PROJECT_ROOT / "tests" / "benchmark_papers"  # JSON试卷目录
CONFIG_PATH = PROJECT_ROOT / "tests" / "benchmark_config.json"  # Excel数据集配置
BASELINE_PATH = PROJECT_ROOT / "tests" / "benchmark_baseline.json"
HISTORY_PATH = PROJECT_ROOT / "data" / "benchmark_history.json"
PAPER_OVERRIDES_PATH = PAPERS_DIR / "_paper_overrides.json"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 第一部分：JSON试卷模式（答案对比，算命中率）
# ============================================================

def load_paper_overrides() -> dict:
    """Load per-paper disable rules produced by integrity audit."""
    if not PAPER_OVERRIDES_PATH.exists():
        return {}
    try:
        data = json.loads(PAPER_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    disabled = data.get("disabled_papers", {})
    return disabled if isinstance(disabled, dict) else {}


def load_json_papers(province_filter: str = None,
                     include_disabled: bool = False) -> tuple[dict, list[dict]]:
    """加载JSON固定试卷

    返回: ({省份名: {province, items, ...}, ...}, skipped_papers)
    """
    papers = {}
    skipped_papers = []
    if not PAPERS_DIR.exists():
        return papers, skipped_papers

    disabled_papers = {} if include_disabled else load_paper_overrides()

    for fpath in sorted(PAPERS_DIR.glob("*.json")):
        if fpath.name.startswith('_'):
            continue
        if fpath.name in disabled_papers:
            skipped_papers.append({
                "paper": fpath.name,
                "province": disabled_papers[fpath.name].get("province", ""),
                "reason": disabled_papers[fpath.name].get("reason", "disabled"),
            })
            continue
        data = json.loads(fpath.read_text(encoding="utf-8"))
        prov = data.get('province')
        if not prov:
            continue
        if province_filter and province_filter not in prov:
            continue
        # 同省多份试卷（如正常卷+脏数据卷）合并items，不覆盖
        if prov in papers:
            papers[prov]['items'].extend(data.get('items', []))
        else:
            papers[prov] = data
    return papers, skipped_papers


def run_json_paper(province: str, items: list[dict]) -> dict:
    """对一个省份的JSON试卷运行搜索匹配，对比标准答案

    返回: {province, total, correct, wrong, hit_rate, diagnosis, elapsed, details}
    """
    from src.match_engine import init_search_components, match_search_only
    from src.text_parser import TextParser

    # 初始化搜索引擎
    searcher, validator = init_search_components(resolved_province=province)
    parser = TextParser()  # noqa: F841 — 保留供后续扩展

    # 构建bill_items
    bill_items = []
    card_map = {}
    for i, item in enumerate(items):
        bill_name = item['bill_name']
        bill_text = item['bill_text']

        if not bill_name:
            first_line = bill_text.split('\n')[0].strip()
            for prefix in ('名称:', '名称：'):
                if prefix in first_line:
                    bill_name = first_line.split(prefix)[-1].strip().split()[0]
                    break
            else:
                parts = first_line.split()
                bill_name = parts[0] if parts else first_line[:20]

        bill_items.append({
            'name': bill_name,
            'description': bill_text,
            'unit': '',
            'quantity': 1,
            'seq': i + 1,
            'specialty': item.get('specialty', ''),
        })
        card_map[i + 1] = item

    # 运行搜索匹配
    start = time.time()
    results = match_search_only(
        bill_items, searcher, validator,
        experience_db=None, province=province)
    elapsed = time.time() - start

    # 逐条对比标准答案
    correct = 0
    wrong = 0
    diagnosis = Counter()
    # oracle统计：错误中，正确答案是否在候选列表里（排序问题 vs 召回问题）
    oracle_in_candidates = 0  # 正确答案在候选中（排序问题）
    oracle_not_in_candidates = 0  # 正确答案不在候选中（召回问题）
    details = []

    for result in results:
        bi = result.get('bill_item', {})
        seq = bi.get('seq', 0)
        card = card_map.get(seq)
        if not card:
            continue

        source = result.get('match_source', '')
        if source == 'skip_measure':
            continue

        quotas = result.get('quotas', [])
        algo_id = quotas[0]['quota_id'] if quotas else ''
        algo_name = quotas[0].get('name', '') if quotas else ''
        confidence = result.get('confidence', 0)
        stored_ids = card['quota_ids']

        is_match = algo_id in stored_ids if (algo_id and stored_ids) else False

        # 检查正确答案是否在候选列表中（oracle诊断）
        all_cand_ids = result.get('all_candidate_ids', [])
        oracle_found = any(sid in all_cand_ids for sid in stored_ids) if stored_ids else False

        if is_match:
            correct += 1
        else:
            wrong += 1
            cause = _diagnose_cause(card, algo_id, algo_name, quotas)
            diagnosis[cause] += 1
            if oracle_found:
                oracle_in_candidates += 1
            else:
                oracle_not_in_candidates += 1

        details.append({
            'bill_name': card['bill_name'][:30],
            'is_match': is_match,
            'algo_id': algo_id,
            'algo_name': algo_name[:30],
            'stored_ids': stored_ids[:2],
            'stored_names': card['quota_names'][:1],
            'confidence': confidence,
            'oracle_in_candidates': oracle_found,
            'all_candidate_ids': all_cand_ids[:10],
        })

    total = correct + wrong
    hit_rate = correct / max(total, 1) * 100

    return {
        'province': province,
        'total': total,
        'correct': correct,
        'wrong': wrong,
        'hit_rate': round(hit_rate, 1),
        'diagnosis': dict(diagnosis),
        'oracle_in_candidates': oracle_in_candidates,
        'oracle_not_in_candidates': oracle_not_in_candidates,
        'elapsed': round(elapsed, 1),
        'details': details,
    }


def _diagnose_cause(card, algo_id, algo_name, quotas):
    """错误根因诊断"""
    if not quotas:
        return 'no_result'

    stored_first = card['quota_names'][0] if card['quota_names'] else ''
    stored_keywords = set(stored_first.replace('(', ' ').replace(')', ' ').split())
    algo_keywords = set(algo_name.replace('(', ' ').replace(')', ' ').split())
    ignore = {'安装', '制作', '周长', 'mm', 'm2', '以内', '≤'}
    stored_keywords -= ignore
    algo_keywords -= ignore

    # 检查专业册是否一致
    def get_book(qid):
        if len(qid) >= 2 and qid[0] == 'C' and qid[1].isalpha():
            letter_map = {'A': 'C1', 'B': 'C2', 'C': 'C3', 'D': 'C4',
                          'E': 'C5', 'F': 'C6', 'G': 'C7', 'H': 'C8',
                          'I': 'C9', 'J': 'C10', 'K': 'C11', 'L': 'C12'}
            return letter_map.get(qid[1], '')
        m = re.match(r'(C\d+)-', qid)
        if m:
            return m.group(1)
        m = re.match(r'(\d+)-', qid)
        if m:
            return f'C{m.group(1)}'
        return ''

    stored_id = card['quota_ids'][0] if card['quota_ids'] else ''
    if stored_id and algo_id:
        if get_book(stored_id) and get_book(algo_id) and get_book(stored_id) != get_book(algo_id):
            return 'wrong_book'

    # 同族判断
    family_overlap = stored_keywords & algo_keywords
    if len(family_overlap) > 0:
        return 'wrong_tier'

    return 'synonym_gap'


# ============================================================
# 第二部分：Excel数据集模式（置信度，算绿/黄/红率）
# ============================================================

def load_excel_config() -> dict:
    """加载Excel数据集配置"""
    if not CONFIG_PATH.exists():
        return {}
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return config.get("datasets", {})


def run_excel_dataset(name: str, ds_config: dict, mode: str) -> dict | None:
    """运行单个Excel数据集的benchmark"""
    path = ds_config["path"]
    if not Path(path).is_absolute():
        path = str(PROJECT_ROOT / path)
    if not Path(path).exists():
        return None

    province = ds_config.get("province", "北京2024")

    try:
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        os.environ.setdefault("TQDM_DISABLE", "1")
        import main as main_module
        try:
            log_level = os.getenv("BENCHMARK_LOG_LEVEL", "WARNING").upper()
            main_module.logger.remove()
            main_module.logger.add(sys.stderr, level=log_level)
        except Exception:
            pass

        start = time.time()
        result = main_module.run(
            input_file=path, mode=mode, province=province, interactive=False)
        elapsed = time.time() - start

        results = result.get("results", [])
        return _compute_excel_metrics(results, elapsed)
    except Exception as e:
        return {"_failed": True, "error": str(e)}


def _compute_excel_metrics(results: list[dict], elapsed: float) -> dict:
    """从Excel匹配结果计算置信度指标"""
    total_all = len(results)
    if total_all == 0:
        return {"total": 0, "green_rate": 0, "yellow_rate": 0, "red_rate": 0,
                "exp_hit_rate": 0, "avg_time_sec": 0}

    def _conf(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    skip = sum(1 for r in results if r.get("match_source") == "skip_measure")
    total = total_all - skip
    matchable = [r for r in results if r.get("match_source") != "skip_measure"]
    high = sum(1 for r in matchable if _conf(r.get("confidence", 0)) >= 85)
    mid = sum(1 for r in matchable if 60 <= _conf(r.get("confidence", 0)) < 85)
    low = total - high - mid
    exp_hits = sum(1 for r in results if str(r.get("match_source", "")).startswith("experience"))

    denom = max(total, 1)
    return {
        "total": total_all, "skip_measure": skip,
        "green_rate": round(high / denom, 4),
        "yellow_rate": round(mid / denom, 4),
        "red_rate": round(low / denom, 4),
        "exp_hit_rate": round(exp_hits / total_all, 4),
        "avg_time_sec": round(elapsed / total_all, 2),
    }


# ============================================================
# 第三部分：汇总打印 + 基线管理
# ============================================================

def format_rate(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def print_json_summary(results: list[dict], baseline: dict = None):
    """打印JSON试卷的汇总表"""
    print(f"\n{'='*90}")
    print("跨省Benchmark（纯搜索模式，不含经验库）")
    print(f"{'='*90}")

    header = f"{'省份':<20} {'题数':>5} {'命中率':>8}"
    if baseline:
        header += f" {'基线':>8} {'变化':>8}"
    header += f" {'同义词缺口':>10} {'选错档位':>10} {'搜偏':>6} {'oracle在候选':>12} {'耗时':>7}"
    print(header)
    print('-' * 110)

    total_correct = 0
    total_items = 0
    total_oracle_in = 0
    total_oracle_out = 0

    for r in results:
        prov_short = r['province'].split('(')[0].split('（')[0][:18]
        diag = r['diagnosis']
        syn_gap = diag.get('synonym_gap', 0)
        wrong_tier = diag.get('wrong_tier', 0)
        wrong_book = diag.get('wrong_book', 0)
        oracle_in = r.get('oracle_in_candidates', 0)
        oracle_out = r.get('oracle_not_in_candidates', 0)

        line = f"{prov_short:<20} {r['total']:>5} {r['hit_rate']:>7.1f}%"

        if baseline and r['province'] in baseline.get('json_papers', {}):
            base_rate = baseline['json_papers'][r['province']]['hit_rate']
            delta = r['hit_rate'] - base_rate
            sign = '+' if delta > 0 else ''
            line += f" {base_rate:>7.1f}% {sign}{delta:>6.1f}%"
        elif baseline:
            line += f" {'新':>8} {'':>8}"

        # oracle统计：错误中有多少正确答案在候选列表里
        oracle_str = f"{oracle_in}/{oracle_in+oracle_out}" if (oracle_in + oracle_out) > 0 else "-"
        line += f" {syn_gap:>10} {wrong_tier:>10} {wrong_book:>6} {oracle_str:>12} {r['elapsed']:>6.1f}s"
        print(line)

        total_correct += r['correct']
        total_items += r['total']
        total_oracle_in += oracle_in
        total_oracle_out += oracle_out

    overall_rate = total_correct / max(total_items, 1) * 100
    print('-' * 110)
    total_oracle = total_oracle_in + total_oracle_out
    oracle_summary = f"{total_oracle_in}/{total_oracle}" if total_oracle > 0 else "-"
    print(f"{'总计':<20} {total_items:>5} {overall_rate:>7.1f}%")

    # 错误分布汇总
    all_diag = Counter()
    for r in results:
        for k, v in r['diagnosis'].items():
            all_diag[k] += v
    total_errors = sum(all_diag.values())
    if total_errors > 0:
        print(f"\n错误分布: ", end='')
        for cause, cnt in all_diag.most_common():
            pct = cnt / total_errors * 100
            label = {'synonym_gap': '同义词缺口', 'wrong_tier': '选错档位',
                     'wrong_book': '搜偏专业', 'no_result': '无结果'}.get(cause, cause)
            print(f"{label} {cnt}({pct:.0f}%) ", end='')
        print()

    # oracle汇总：排序问题 vs 召回问题
    if total_oracle > 0:
        oracle_rate = total_oracle_in / total_oracle * 100
        print(f"\noracle诊断: 错误共{total_oracle}条，"
              f"其中{total_oracle_in}条({oracle_rate:.0f}%)正确答案在候选中(排序问题)，"
              f"{total_oracle_out}条({100-oracle_rate:.0f}%)不在候选中(召回问题)")

    print(f"{'='*110}")
    return {'total': total_items, 'correct': total_correct, 'rate': round(overall_rate, 1)}


def print_excel_summary(excel_metrics: dict[str, dict]):
    """打印Excel数据集的汇总表"""
    has_any = any(m is not None for m in excel_metrics.values())
    if not has_any:
        return

    print(f"\n{'='*80}")
    print("Excel数据集Benchmark（置信度模式）")
    print(f"{'='*80}")
    print(f"{'数据集':<20s} {'总数':>5s} {'绿率':>7s} {'黄率':>7s} "
          f"{'红率':>7s} {'经验命中':>8s} {'均耗':>7s}")
    print("-" * 80)

    for name, m in excel_metrics.items():
        if m is None:
            print(f"{name:<20s}  跳过（文件不存在）")
            continue
        if m.get("_failed"):
            print(f"{name:<20s}  失败: {str(m.get('error', ''))[:40]}")
            continue
        skip = m.get('skip_measure', 0)
        skip_str = f"(-{skip})" if skip > 0 else ""
        print(f"{name:<20s} {m['total']:5d}{skip_str:<5s}"
              f"{format_rate(m['green_rate']):>7s} "
              f"{format_rate(m['yellow_rate']):>7s} "
              f"{format_rate(m['red_rate']):>7s} "
              f"{format_rate(m['exp_hit_rate']):>8s} "
              f"{m['avg_time_sec']:6.2f}s")
    print("=" * 80)


def load_baseline() -> dict | None:
    if not BASELINE_PATH.exists():
        return None
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def save_baseline(json_results: list[dict], excel_metrics: dict,
                  note: str = ""):
    """保存统一基线"""
    baseline = {
        "version": "unified_v1",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": note,
        # JSON试卷的结果
        "json_papers": {},
        # Excel数据集的结果
        "excel_datasets": {},
    }

    for r in json_results:
        baseline["json_papers"][r['province']] = {
            'total': r['total'],
            'correct': r['correct'],
            'hit_rate': r['hit_rate'],
            'diagnosis': r['diagnosis'],
        }

    for name, m in excel_metrics.items():
        if m is not None and not m.get("_failed"):
            baseline["excel_datasets"][name] = m

    BASELINE_PATH.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] 基线已保存到 {BASELINE_PATH}")

    # 追加历史记录
    _append_history(baseline)


def _append_history(baseline: dict):
    """追加到历史记录"""
    history = []
    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            history = []
    history.append(baseline)
    HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 历史记录已追加（共 {len(history)} 条）")


def show_baseline():
    """显示当前基线"""
    baseline = load_baseline()
    if baseline is None:
        print("基线文件不存在。请先运行: python tools/run_benchmark.py --save")
        return

    print(f"版本: {baseline.get('version', '未知')}")
    print(f"日期: {baseline.get('date', '未知')}")
    print(f"备注: {baseline.get('note', '')}")

    # JSON试卷
    jp = baseline.get('json_papers', {})
    if jp:
        total_items = sum(v['total'] for v in jp.values())
        total_correct = sum(v['correct'] for v in jp.values())
        overall = total_correct / max(total_items, 1) * 100
        print(f"\nJSON试卷: {len(jp)}个省份, {total_items}条, 总命中率{overall:.1f}%")
        for prov, m in sorted(jp.items()):
            prov_short = prov.split('(')[0][:16]
            print(f"  {prov_short}: {m['total']}条, 命中率{m['hit_rate']:.1f}%")

    # Excel数据集（兼容旧格式）
    ed = baseline.get('excel_datasets', baseline.get('datasets', {}))
    if ed:
        print(f"\nExcel数据集: {len(ed)}个")
        for name, m in ed.items():
            if isinstance(m, dict) and 'green_rate' in m:
                print(f"  {name}: {m['total']}条, "
                      f"绿率{format_rate(m['green_rate'])}, "
                      f"红率{format_rate(m['red_rate'])}")


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="统一Benchmark系统 — 跨省试卷 + Excel数据集一起跑",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python tools/run_benchmark.py                    # 跑全部（JSON+Excel）
  python tools/run_benchmark.py --save             # 跑完保存基线
  python tools/run_benchmark.py --compare          # 与基线对比
  python tools/run_benchmark.py --province 广东    # 只跑含"广东"的省份
  python tools/run_benchmark.py --detail           # 打印每题详情
  python tools/run_benchmark.py --excel-only       # 只跑Excel数据集
  python tools/run_benchmark.py --json-only        # 只跑JSON试卷
  python tools/run_benchmark.py --show-baseline    # 查看基线
""")
    parser.add_argument("--province", help="只跑包含此关键词的省份（模糊匹配）")
    parser.add_argument("--mode", choices=["search", "agent"], default="search",
                        help="匹配模式（默认search）")
    parser.add_argument("--save", action="store_true", help="保存为基线")
    parser.add_argument("--compare", action="store_true", help="与基线对比")
    parser.add_argument("--detail", action="store_true", help="打印每题详情")
    parser.add_argument("--show-baseline", action="store_true", help="显示基线")
    parser.add_argument("--excel-only", action="store_true", help="只跑Excel数据集")
    parser.add_argument("--json-only", action="store_true", help="只跑JSON试卷")
    parser.add_argument("--note", default="", help="跑分备注")
    args = parser.parse_args()

    if args.show_baseline:
        show_baseline()
        return 0

    # 压低噪声日志
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("TQDM_DISABLE", "1")
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    baseline = load_baseline() if args.compare else None

    # ---- JSON试卷 ----
    json_results = []
    if not args.excel_only:
        papers, skipped_papers = load_json_papers(province_filter=args.province)
        if papers:
            total_items = sum(len(d.get('items', [])) for d in papers.values())
            print(f"JSON试卷: {len(papers)}个省份, {total_items}条题目")
            if skipped_papers:
                print(f"已跳过 {len(skipped_papers)} 份禁用试卷")
            print("-" * 60)

            for province, data in papers.items():
                items = data['items']
                prov_short = province.split('(')[0].split('（')[0][:16]
                print(f"  测试 {prov_short}（{len(items)}题）...", end='', flush=True)

                result = run_json_paper(province, items)
                json_results.append(result)

                mark = '[OK]' if result['hit_rate'] >= 50 else '[FAIL]'
                print(f" {mark} 命中 {result['correct']}/{result['total']}"
                      f" = {result['hit_rate']:.1f}% ({result['elapsed']:.0f}s)")

                if args.detail:
                    for d in result['details']:
                        m = '[OK]' if d['is_match'] else '[X]'
                        name = d['bill_name'][:20]
                        algo = d['algo_name'][:20]
                        stored = d['stored_names'][0][:20] if d['stored_names'] else '?'
                        print(f"    {m} {name} → {algo} (答案:{stored})")

            print_json_summary(json_results, baseline)
        else:
            print(f"未找到JSON试卷（{PAPERS_DIR}/ 为空）")

    # ---- Excel数据集 ----
    excel_metrics = {}
    if not args.json_only:
        excel_datasets = load_excel_config()
        if excel_datasets:
            print(f"\nExcel数据集: {len(excel_datasets)}个")
            print("-" * 60)
            for name, ds_config in excel_datasets.items():
                path = ds_config["path"]
                if not Path(path).is_absolute():
                    path = str(PROJECT_ROOT / path)
                if not Path(path).exists():
                    print(f"  [跳过] {name}: 文件不存在")
                    excel_metrics[name] = None
                    continue

                print(f"  运行 {name}...", end='', flush=True)
                m = run_excel_dataset(name, ds_config, args.mode)
                excel_metrics[name] = m
                if m and not m.get("_failed"):
                    print(f" 绿率{format_rate(m['green_rate'])} "
                          f"红率{format_rate(m['red_rate'])}")
                elif m and m.get("_failed"):
                    print(f" 失败: {m.get('error', '')[:40]}")

            print_excel_summary(excel_metrics)

    # ---- 基线对比 ----
    if args.compare and baseline:
        print(f"\n{'='*60}")
        print("与基线对比")
        print(f"基线日期: {baseline.get('date', '?')}")

        has_regression = False

        # JSON对比
        if json_results and baseline.get('json_papers'):
            for r in json_results:
                bp = baseline['json_papers'].get(r['province'])
                if not bp:
                    continue
                delta = r['hit_rate'] - bp['hit_rate']
                prov_short = r['province'].split('(')[0][:14]
                sign = '+' if delta > 0 else ''
                status = '退化!' if delta < -2 else '正常'
                if delta < -2:
                    has_regression = True
                print(f"  {prov_short}: {bp['hit_rate']:.1f}% → {r['hit_rate']:.1f}% ({sign}{delta:.1f}%) [{status}]")

        if has_regression:
            print("\n[WARN] 检测到退化！")
        else:
            print("\n[OK] 无退化")
        print("=" * 60)

    # ---- 保存基线 ----
    if args.save:
        save_baseline(json_results, excel_metrics, note=args.note)

    # 保存详细结果（每次都存）
    if json_results:
        result_file = PAPERS_DIR / '_latest_result.json'
        result_file.write_text(json.dumps({
            'run_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'results': json_results,
        }, ensure_ascii=False, indent=2), encoding='utf-8')

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
