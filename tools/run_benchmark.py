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
from collections.abc import Iterable
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
BENCHMARK_ASSET_ROOT = PROJECT_ROOT / "output" / "benchmark_assets"
BENCHMARK_ASSET_ALT_LIMIT = 9
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


INSTALL_PROVINCE_KEYWORDS = ("安装工程", "通用安装", "安装定额")
NON_INSTALL_PROVINCE_KEYWORDS = (
    "房屋建筑",
    "建筑与装饰",
    "建筑装饰",
    "装饰工程",
    "市政工程",
    "园林绿化",
    "仿古建筑",
)
MIXED_PROVINCE_KEYWORDS = ("施工消耗量标准", "消耗量标准", "建设工程")
INSTALL_ITEM_HINTS = (
    "安装",
    "配电箱",
    "配电柜",
    "桥架",
    "电缆",
    "电线",
    "照明",
    "灯具",
    "开关",
    "插座",
    "风管",
    "风口",
    "风阀",
    "喷淋",
    "消防",
    "消火栓",
    "给水",
    "排水",
    "阀门",
    "管道",
    "电机",
    "交换机",
    "网线",
)


def _is_install_province(province: str) -> bool:
    return any(keyword in (province or "") for keyword in INSTALL_PROVINCE_KEYWORDS)


def _is_non_install_province(province: str) -> bool:
    return any(keyword in (province or "") for keyword in NON_INSTALL_PROVINCE_KEYWORDS)


def _is_mixed_province(province: str) -> bool:
    if _is_install_province(province) or _is_non_install_province(province):
        return False
    return any(keyword in (province or "") for keyword in MIXED_PROVINCE_KEYWORDS)


def _classify_province_scope(province: str) -> str:
    if _is_install_province(province):
        return "install"
    if _is_non_install_province(province):
        return "non_install"
    if _is_mixed_province(province):
        return "mixed"
    return "unknown"


def _looks_like_install_quota_id(quota_id: str) -> bool:
    quota_id = str(quota_id or "").strip().upper()
    if not quota_id:
        return False
    if re.match(r"^C\d+-", quota_id):
        return True
    if re.match(r"^C[A-Z]-", quota_id):
        return True
    if quota_id.startswith("BC-"):
        return True
    return False


def _looks_like_install_specialty(specialty: str) -> bool:
    specialty = str(specialty or "").strip().upper()
    return bool(re.match(r"^C\d+$", specialty))


def _looks_like_install_text(text: str) -> bool:
    text = str(text or "")
    return any(keyword in text for keyword in INSTALL_ITEM_HINTS)


def _is_install_item(item: dict, province_scope: str = "unknown") -> bool:
    quota_ids = item.get("quota_ids") or []
    if any(_looks_like_install_quota_id(quota_id) for quota_id in quota_ids):
        return True

    if province_scope in {"install", "mixed"} and _looks_like_install_specialty(item.get("specialty", "")):
        return True

    if province_scope in {"mixed", "unknown"} and _looks_like_install_text(_item_search_text(item)):
        return True

    return province_scope == "install"


def _item_search_text(item: dict) -> str:
    quota_names = " ".join(item.get("quota_names") or [])
    return " ".join(
        part for part in (
            item.get("bill_name", ""),
            item.get("bill_text", ""),
            quota_names,
        ) if part
    )


def filter_json_papers(papers: dict,
                       install_only: bool = False,
                       item_keywords: list[str] | None = None,
                       max_items_per_province: int | None = None) -> dict:
    """对 JSON 试卷做轻量快筛，支持安装卷/题族关键词/每省限量。"""
    filtered = {}
    normalized_keywords = [kw.strip() for kw in (item_keywords or []) if kw and kw.strip()]

    for province, data in papers.items():
        province_scope = _classify_province_scope(province)
        if install_only and province_scope == "non_install":
            continue

        items = list(data.get("items", []))
        if install_only and province_scope != "install":
            items = [
                item for item in items
                if _is_install_item(item, province_scope=province_scope)
            ]
        if normalized_keywords:
            items = [
                item for item in items
                if any(keyword in _item_search_text(item) for keyword in normalized_keywords)
            ]

        if max_items_per_province is not None and max_items_per_province >= 0:
            items = items[:max_items_per_province]

        if not items:
            continue

        filtered[province] = {
            **data,
            "items": items,
        }

    return filtered


def run_json_paper(province: str, items: list[dict],
                   with_experience: bool = False) -> dict:
    """对一个省份的JSON试卷运行搜索匹配，对比标准答案

    返回: {province, total, correct, wrong, hit_rate, diagnosis, elapsed, details}
    with_experience: 为True时启用经验库直通匹配（默认关闭）
    """
    from src.match_engine import init_search_components, match_search_only
    from src.text_parser import TextParser

    # 初始化搜索引擎
    searcher, validator = init_search_components(resolved_province=province)
    parser = TextParser()  # noqa: F841 — 保留供后续扩展

    # 经验库（默认关闭，--with-experience 打开）
    experience_db = None
    if with_experience:
        try:
            from src.experience_db import ExperienceDB
            experience_db = ExperienceDB(province=province)
        except Exception:
            pass  # 加载失败则跳过

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
        experience_db=experience_db, province=province)
    elapsed = time.time() - start

    # 逐条对比标准答案
    correct = 0
    wrong = 0
    diagnosis = Counter()
    recall_miss_count = 0
    rank_miss_count = 0
    post_rank_miss_count = 0
    in_pool_total = 0
    in_pool_correct = 0
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
        pre_ltr_top1_id = str(result.get('pre_ltr_top1_id', '') or '')
        post_ltr_top1_id = str(result.get('post_ltr_top1_id', '') or '')
        post_arbiter_top1_id = str(result.get('post_arbiter_top1_id', '') or '')
        post_final_top1_id = str(result.get('post_final_top1_id', algo_id) or algo_id or '')
        final_changed_by = str(result.get('final_changed_by', '') or '')

        if oracle_found:
            in_pool_total += 1
            if is_match:
                in_pool_correct += 1

        if is_match:
            correct += 1
            cause = ""
        else:
            wrong += 1
            cause = _diagnose_cause(card, algo_id, algo_name, quotas)
            diagnosis[cause] += 1
            if oracle_found:
                oracle_in_candidates += 1
                if post_ltr_top1_id and post_ltr_top1_id in stored_ids and post_final_top1_id not in stored_ids:
                    post_rank_miss_count += 1
                else:
                    rank_miss_count += 1
            else:
                oracle_not_in_candidates += 1
                recall_miss_count += 1

        miss_stage = ""
        if not is_match:
            if not oracle_found:
                miss_stage = "recall_miss"
            elif post_ltr_top1_id and post_ltr_top1_id in stored_ids and post_final_top1_id not in stored_ids:
                miss_stage = "post_rank_miss"
            else:
                miss_stage = "rank_miss"

        details.append({
            'bill_name': card['bill_name'][:30],
            'is_match': is_match,
            'algo_id': algo_id,
            'algo_name': algo_name[:30],
            'stored_ids': stored_ids[:2],
            'stored_names': card['quota_names'][:1],
            'confidence': confidence,
            'cause': cause,
            'oracle_in_candidates': oracle_found,
            'all_candidate_ids': all_cand_ids[:10],
            'bill_text': card['bill_text'],
            'specialty': card.get('specialty', ''),
            'match_source': result.get('match_source', ''),
            'no_match_reason': result.get('no_match_reason', ''),
            'reasoning_decision': result.get('reasoning_decision', {}),
            'alternatives': result.get('alternatives', [])[:BENCHMARK_ASSET_ALT_LIMIT],
            'candidate_snapshots': result.get('candidate_snapshots', [])[:20],
            'trace_path': list((result.get('trace') or {}).get('path') or []),
            'candidate_count': result.get('candidate_count', result.get('candidates_count', len(all_cand_ids))),
            'pre_ltr_top1_id': pre_ltr_top1_id,
            'post_ltr_top1_id': post_ltr_top1_id,
            'post_arbiter_top1_id': post_arbiter_top1_id,
            'post_final_top1_id': post_final_top1_id,
            'final_changed_by': final_changed_by,
            'miss_stage': miss_stage,
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
        'in_pool_top1_acc': round(in_pool_correct / max(in_pool_total, 1), 4),
        'recall_miss_count': recall_miss_count,
        'rank_miss_count': rank_miss_count,
        'post_rank_miss_count': post_rank_miss_count,
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


def _get_quota_book(qid: str) -> str:
    qid = str(qid or "").strip()
    if len(qid) >= 2 and qid[0] == 'C' and qid[1].isalpha():
        letter_map = {'A': 'C1', 'B': 'C2', 'C': 'C3', 'D': 'C4',
                      'E': 'C5', 'F': 'C6', 'G': 'C7', 'H': 'C8',
                      'I': 'C9', 'J': 'C10', 'K': 'C11', 'L': 'C12'}
        return letter_map.get(qid[1], '')
    match = re.match(r'(C\d+)-', qid)
    if match:
        return match.group(1)
    match = re.match(r'(\d+)-', qid)
    if match:
        return f'C{match.group(1)}'
    return ''


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


def _normalize_asset_candidates(selected_id: str, selected_name: str,
                                alternatives: Iterable[dict] | None) -> list[dict]:
    candidates: list[dict] = []
    if selected_id or selected_name:
        candidates.append({
            "quota_id": str(selected_id or ""),
            "name": str(selected_name or ""),
            "is_selected": True,
        })
    for alt in list(alternatives or [])[:BENCHMARK_ASSET_ALT_LIMIT]:
        candidates.append({
            "quota_id": str(alt.get("quota_id", "") or ""),
            "name": str(alt.get("name", "") or ""),
            "is_selected": False,
            "reasoning": alt.get("reasoning", {}),
        })
    return candidates


def _iter_benchmark_error_records(json_results: list[dict]) -> Iterable[dict]:
    for province_result in json_results or []:
        province = province_result.get("province", "")
        for detail in province_result.get("details", []) or []:
            if detail.get("is_match"):
                continue
            stored_ids = list(detail.get("stored_ids") or [])
            stored_names = list(detail.get("stored_names") or [])
            algo_id = str(detail.get("algo_id", "") or "")
            algo_name = str(detail.get("algo_name", "") or "")
            cause = str(detail.get("cause", "") or "")
            yield {
                "province": province,
                "cause": cause,
                "bill_name": str(detail.get("bill_name", "") or ""),
                "bill_text": str(detail.get("bill_text", "") or ""),
                "specialty": str(detail.get("specialty", "") or ""),
                "expected_quota_ids": stored_ids,
                "expected_quota_names": stored_names,
                "expected_book": _get_quota_book(stored_ids[0]) if stored_ids else "",
                "predicted_quota_id": algo_id,
                "predicted_quota_name": algo_name,
                "predicted_book": _get_quota_book(algo_id),
                "confidence": float(detail.get("confidence", 0) or 0),
                "oracle_in_candidates": bool(detail.get("oracle_in_candidates", False)),
                "all_candidate_ids": list(detail.get("all_candidate_ids") or []),
                "retrieved_candidates": _normalize_asset_candidates(
                    algo_id,
                    algo_name,
                    detail.get("alternatives") or [],
                ),
                "candidate_snapshots": list(detail.get("candidate_snapshots") or []),
                "reasoning_decision": detail.get("reasoning_decision", {}),
                "trace_path": list(detail.get("trace_path") or []),
                "match_source": str(detail.get("match_source", "") or ""),
                "no_match_reason": str(detail.get("no_match_reason", "") or ""),
                "candidate_count": int(detail.get("candidate_count", 0) or 0),
                "pre_ltr_top1_id": str(detail.get("pre_ltr_top1_id", "") or ""),
                "post_ltr_top1_id": str(detail.get("post_ltr_top1_id", "") or ""),
                "post_arbiter_top1_id": str(detail.get("post_arbiter_top1_id", "") or ""),
                "post_final_top1_id": str(detail.get("post_final_top1_id", "") or ""),
                "final_changed_by": str(detail.get("final_changed_by", "") or ""),
                "miss_stage": str(detail.get("miss_stage", "") or ""),
            }


def _build_benchmark_asset_buckets(json_results: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {
        "all_errors": [],
        "rerank_pairs": [],
        "synonym_gaps": [],
        "route_errors": [],
        "tier_errors": [],
    }
    for record in _iter_benchmark_error_records(json_results):
        buckets["all_errors"].append(record)

        if record["oracle_in_candidates"]:
            buckets["rerank_pairs"].append({
                "province": record["province"],
                "bill_name": record["bill_name"],
                "bill_text": record["bill_text"],
                "specialty": record["specialty"],
                "positive_quota_ids": record["expected_quota_ids"],
                "positive_quota_names": record["expected_quota_names"],
                "negative_quota_id": record["predicted_quota_id"],
                "negative_quota_name": record["predicted_quota_name"],
                "cause": record["cause"],
                "retrieved_candidates": record["retrieved_candidates"],
            })

        if record["cause"] == "synonym_gap":
            buckets["synonym_gaps"].append(record)
        elif record["cause"] == "wrong_book":
            buckets["route_errors"].append(record)
        elif record["cause"] == "wrong_tier":
            buckets["tier_errors"].append(record)
    return buckets


def export_benchmark_assets(json_results: list[dict], output_dir: str = "") -> Path | None:
    if not json_results:
        return None

    buckets = _build_benchmark_asset_buckets(json_results)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    asset_dir = Path(output_dir) if output_dir else (BENCHMARK_ASSET_ROOT / timestamp)
    asset_dir.mkdir(parents=True, exist_ok=True)

    file_map = {
        "all_errors": "all_errors.jsonl",
        "rerank_pairs": "rerank_pairs.jsonl",
        "synonym_gaps": "synonym_gaps.jsonl",
        "route_errors": "route_errors.jsonl",
        "tier_errors": "tier_errors.jsonl",
    }
    manifest = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(asset_dir),
        "counts": {},
        "files": {},
    }

    for bucket_name, filename in file_map.items():
        records = buckets.get(bucket_name, [])
        path = asset_dir / filename
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        manifest["counts"][bucket_name] = len(records)
        manifest["files"][bucket_name] = str(path)

    (asset_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return asset_dir


def materialize_benchmark_learning_outputs(
    asset_dir: str | Path,
    *,
    knowledge_out: str | Path | None = None,
    digest_out: str | Path | None = None,
    digest_md_out: str | Path | None = None,
    training_out_root: str | Path | None = None,
) -> dict[str, str]:
    """将 benchmark 资产同步产出为运行时知识和训练数据。"""
    from tools.build_benchmark_knowledge import (
        DEFAULT_DIGEST_MD_OUT_PATH,
        DEFAULT_DIGEST_OUT_PATH,
        DEFAULT_OUT_PATH,
        build_knowledge_digest,
        build_knowledge_from_asset_root,
        write_digest,
        write_digest_markdown,
        write_knowledge,
    )
    from tools.export_benchmark_training_data import (
        DEFAULT_OUT_ROOT as DEFAULT_TRAIN_OUT_ROOT,
        export_training_datasets,
    )

    asset_dir = Path(asset_dir)
    knowledge = build_knowledge_from_asset_root(asset_dir)
    knowledge_path = write_knowledge(knowledge, knowledge_out or DEFAULT_OUT_PATH)
    digest = build_knowledge_digest(knowledge)
    digest_path = write_digest(digest, digest_out or DEFAULT_DIGEST_OUT_PATH)
    digest_md_path = write_digest_markdown(digest, digest_md_out or DEFAULT_DIGEST_MD_OUT_PATH)
    training_manifest_path, _ = export_training_datasets(asset_dir, training_out_root or DEFAULT_TRAIN_OUT_ROOT)

    return {
        "knowledge_path": str(knowledge_path),
        "digest_path": str(digest_path),
        "digest_md_path": str(digest_md_path),
        "training_manifest_path": str(training_manifest_path),
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

        base_rate = _get_baseline_json_hit_rate(baseline, r['province']) if baseline else None
        if base_rate is not None:
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


def _build_json_overall(json_results: list[dict]) -> dict:
    total_items = sum(r.get('total', 0) for r in json_results)
    total_correct = sum(r.get('correct', 0) for r in json_results)
    total_recall_miss = sum(r.get('recall_miss_count', 0) for r in json_results)
    total_rank_miss = sum(r.get('rank_miss_count', 0) for r in json_results)
    total_post_rank_miss = sum(r.get('post_rank_miss_count', 0) for r in json_results)
    total_oracle_in = sum(r.get('oracle_in_candidates', 0) for r in json_results)
    weighted_in_pool_num = sum(
        float(r.get('in_pool_top1_acc', 0.0)) * float(r.get('oracle_in_candidates', 0))
        for r in json_results
    )
    hit_rate = round(total_correct / max(total_items, 1) * 100, 1)
    return {
        "total": total_items,
        "correct": total_correct,
        "hit_rate": hit_rate,
        "recall_miss_count": total_recall_miss,
        "rank_miss_count": total_rank_miss,
        "post_rank_miss_count": total_post_rank_miss,
        "in_pool_top1_acc": round(weighted_in_pool_num / max(total_oracle_in, 1), 4),
    }


def build_benchmark_summary(json_results: list[dict], excel_metrics: dict,
                            baseline: dict | None = None) -> dict:
    """构建机器可读的 benchmark 摘要，供 loop runner 判定 keep/discard。"""
    return {
        "json_overall": _build_json_overall(json_results),
        "json_results": json_results,
        "excel_metrics": excel_metrics,
        "by_province": _build_by_province_summary(json_results, baseline),
    }


def _build_by_province_summary(json_results: list[dict],
                               previous_baseline: dict | None = None) -> dict:
    previous = (previous_baseline or {}).get("by_province", {})
    summary = {}
    for result in json_results:
        province = result['province']
        previous_status = ""
        if isinstance(previous.get(province), dict):
            previous_status = previous[province].get("status", "")
        summary[province] = {
            "score": round(result['hit_rate'] / 100, 4),
            "hit_rate": result['hit_rate'],
            "total": result['total'],
            "correct": result['correct'],
            "in_pool_top1_acc": result.get('in_pool_top1_acc', 0.0),
            "recall_miss_count": result.get('recall_miss_count', 0),
            "rank_miss_count": result.get('rank_miss_count', 0),
            "post_rank_miss_count": result.get('post_rank_miss_count', 0),
            "status": previous_status,
        }
    return summary


def _get_baseline_json_hit_rate(baseline: dict | None, province: str) -> float | None:
    if not baseline:
        return None
    by_province = baseline.get("by_province", {})
    if isinstance(by_province.get(province), dict):
        hit_rate = by_province[province].get("hit_rate")
        if isinstance(hit_rate, (int, float)):
            return float(hit_rate)
        score = by_province[province].get("score")
        if isinstance(score, (int, float)):
            return float(score) * 100.0
    json_papers = baseline.get("json_papers", {})
    if isinstance(json_papers.get(province), dict):
        hit_rate = json_papers[province].get("hit_rate")
        if isinstance(hit_rate, (int, float)):
            return float(hit_rate)
    return None


def save_baseline(json_results: list[dict], excel_metrics: dict,
                  note: str = ""):
    """保存统一基线"""
    previous_baseline = load_baseline()
    overall_json = _build_json_overall(json_results)
    baseline = {
        "version": "unified_v1",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": note,
        "overall": {
            "json_total": overall_json["total"],
            "json_correct": overall_json["correct"],
            "json_hit_rate": overall_json["hit_rate"],
        },
        "by_province": _build_by_province_summary(json_results, previous_baseline),
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

    overall = baseline.get('overall', {})
    if overall:
        print(f"总览: JSON {overall.get('json_correct', 0)}/{overall.get('json_total', 0)} "
              f"= {overall.get('json_hit_rate', 0):.1f}%")

    # JSON试卷
    jp = baseline.get('json_papers', {})
    if jp:
        total_items = sum(v['total'] for v in jp.values())
        total_correct = sum(v['correct'] for v in jp.values())
        overall = total_correct / max(total_items, 1) * 100
        print(f"\nJSON试卷: {len(jp)}个省份, {total_items}条, 总命中率{overall:.1f}%")
        province_view = baseline.get('by_province', {}) or jp
        for prov, m in sorted(province_view.items()):
            prov_short = prov.split('(')[0][:16]
            total = m.get('total', 0)
            hit_rate = m.get('hit_rate')
            if hit_rate is None and isinstance(m.get('score'), (int, float)):
                hit_rate = m['score'] * 100
            if hit_rate is None:
                hit_rate = 0.0
            status = m.get('status', '') if isinstance(m, dict) else ''
            suffix = f" [{status}]" if status else ''
            print(f"  {prov_short}: {total}条, 命中率{hit_rate:.1f}%{suffix}")

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
    parser.add_argument("--install-only", action="store_true",
                        help="只跑安装类 JSON 试卷（用于阶段二快筛）")
    parser.add_argument("--item-keyword", action="append",
                        help="只跑包含该关键词的题目，可重复传入（匹配 bill_name/bill_text/答案）")
    parser.add_argument("--max-items-per-province", type=int,
                        help="每省最多取前 N 题，配合 --item-keyword 用于快速试错")
    parser.add_argument("--save", action="store_true", help="保存为基线")
    parser.add_argument("--compare", action="store_true", help="与基线对比")
    parser.add_argument("--detail", action="store_true", help="打印每题详情")
    parser.add_argument("--show-baseline", action="store_true", help="显示基线")
    parser.add_argument("--excel-only", action="store_true", help="只跑Excel数据集")
    parser.add_argument("--json-only", action="store_true", help="只跑JSON试卷")
    parser.add_argument("--with-experience", action="store_true",
                        help="启用经验库直通（默认关闭，打开后看经验库对分数的影响）")
    parser.add_argument("--note", default="", help="跑分备注")
    parser.add_argument("--summary-json-out", default="",
                        help="把机器可读摘要写到指定 JSON 文件，供 loop runner 使用")
    parser.add_argument("--asset-out-dir", default="",
                        help="export benchmark error assets as JSONL")
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
        papers = filter_json_papers(
            papers,
            install_only=args.install_only,
            item_keywords=args.item_keyword,
            max_items_per_province=args.max_items_per_province,
        )
        if papers:
            total_items = sum(len(d.get('items', [])) for d in papers.values())
            print(f"JSON试卷: {len(papers)}个省份, {total_items}条题目")
            if skipped_papers:
                print(f"已跳过 {len(skipped_papers)} 份禁用试卷")
            if args.install_only or args.item_keyword or args.max_items_per_province is not None:
                active_filters = []
                if args.install_only:
                    active_filters.append("install_only")
                if args.item_keyword:
                    active_filters.append(f"item_keywords={','.join(args.item_keyword)}")
                if args.max_items_per_province is not None:
                    active_filters.append(f"max_items_per_province={args.max_items_per_province}")
                print(f"筛选条件: {'; '.join(active_filters)}")
            print("-" * 60)

            for province, data in papers.items():
                items = data['items']
                prov_short = province.split('(')[0].split('（')[0][:16]
                print(f"  测试 {prov_short}（{len(items)}题）...", end='', flush=True)

                result = run_json_paper(province, items,
                                        with_experience=args.with_experience)
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
        if json_results and (baseline.get('json_papers') or baseline.get('by_province')):
            for r in json_results:
                base_rate = _get_baseline_json_hit_rate(baseline, r['province'])
                if base_rate is None:
                    continue
                delta = r['hit_rate'] - base_rate
                prov_short = r['province'].split('(')[0][:14]
                sign = '+' if delta > 0 else ''
                status = '退化!' if delta < -2 else '正常'
                if delta < -2:
                    has_regression = True
                print(f"  {prov_short}: {base_rate:.1f}% → {r['hit_rate']:.1f}% ({sign}{delta:.1f}%) [{status}]")

        if has_regression:
            print("\n[WARN] 检测到退化！")
        else:
            print("\n[OK] 无退化")
        print("=" * 60)

    # ---- 保存基线 ----
    if args.save:
        save_baseline(json_results, excel_metrics, note=args.note)

    if args.summary_json_out:
        summary = build_benchmark_summary(json_results, excel_metrics, baseline)
        summary_path = Path(args.summary_json_out)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # 保存详细结果（每次都存）
    if json_results:
        asset_dir = export_benchmark_assets(json_results, args.asset_out_dir)
        if asset_dir:
            print(f"\n[ASSET] benchmark assets exported to: {asset_dir}")
            learning_outputs = materialize_benchmark_learning_outputs(asset_dir)
            print(f"[ASSET] benchmark knowledge updated: {learning_outputs['knowledge_path']}")
            print(f"[ASSET] benchmark digest updated: {learning_outputs['digest_path']}")
            print(f"[ASSET] benchmark training manifest: {learning_outputs['training_manifest_path']}")
        result_file = PAPERS_DIR / '_latest_result.json'
        result_file.write_text(json.dumps({
            'run_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'results': json_results,
        }, ensure_ascii=False, indent=2), encoding='utf-8')

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
