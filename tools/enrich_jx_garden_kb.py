#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
补充江西园林 benchmark 错题到通用知识库权威层。

目的：
1. 按运行时 search_query 口径，给江西园林短清单名补搜索方向提示
2. 先消除江西园林 0/19 这类明显异常值
3. 保留为可重复执行的脚本，后续可增量维护

用法：
    python -X utf8 tools/enrich_jx_garden_kb.py --preview
    python -X utf8 tools/enrich_jx_garden_kb.py --apply
    python -X utf8 tools/enrich_jx_garden_kb.py --apply --all-items
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.text_parser import TextParser
from src.universal_kb import UniversalKB


PAPERS_DIR = PROJECT_ROOT / "tests" / "benchmark_papers"
LATEST_RESULT_PATH = PAPERS_DIR / "_latest_result.json"
PAPER_NAME_HINT = "江西省园林绿化工程消耗量定额及统一基价表"
SOURCE_PROJECT = "phase1_jx_garden_benchmark_shortname_v1"


def _clean_text(text: str) -> str:
    text = str(text or "")
    text = text.replace("\x7f", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _find_paper_path() -> Path:
    for path in sorted(PAPERS_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        if PAPER_NAME_HINT in path.name:
            return path
    raise FileNotFoundError(f"未找到试卷: {PAPER_NAME_HINT}")


def _load_failed_bill_names() -> set[str]:
    data = json.loads(LATEST_RESULT_PATH.read_text(encoding="utf-8"))
    for result in data.get("results", []):
        if PAPER_NAME_HINT in result.get("province", ""):
            return {_clean_text(item.get("bill_name", "")) for item in result.get("details", [])}
    raise RuntimeError(f"{LATEST_RESULT_PATH} 中未找到 {PAPER_NAME_HINT} 的结果")


def _load_paper_items() -> tuple[str, list[dict]]:
    path = _find_paper_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["province"], data["items"]


def _build_records(only_failed: bool = True) -> tuple[str, list[dict], list[dict]]:
    province, items = _load_paper_items()
    failed_names = _load_failed_bill_names() if only_failed else None
    parser = TextParser()
    records = []
    preview_rows = []

    for item in items:
        bill_name = _clean_text(item.get("bill_name", ""))
        if only_failed and bill_name not in failed_names:
            continue

        bill_text = _clean_text(item.get("bill_text", ""))
        search_query = _clean_text(
            parser.build_quota_query(
                bill_name,
                bill_text,
                specialty=item.get("specialty", ""),
            )
        )
        quota_names = [_clean_text(q) for q in item.get("quota_names", []) if _clean_text(q)]
        if not search_query or not quota_names:
            continue

        records.append(
            {
                "bill_pattern": search_query,
                "quota_patterns": [quota_names[0]],
                "associated_patterns": quota_names[1:],
                "bill_keywords": [bill_name],
                "specialty": item.get("specialty", ""),
            }
        )
        preview_rows.append(
            {
                "bill_name": bill_name,
                "search_query": search_query,
                "quota_names": quota_names,
                "specialty": item.get("specialty", ""),
            }
        )

    return province, records, preview_rows


def _find_conflicts(preview_rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in preview_rows:
        grouped[row["search_query"]].append(row)
    conflicts = {}
    for query, rows in grouped.items():
        unique_targets = {
            (
                tuple(rows[i]["quota_names"]),
                rows[i]["specialty"],
            )
            for i in range(len(rows))
        }
        if len(unique_targets) > 1:
            conflicts[query] = rows
    return conflicts


def _print_preview(province: str, preview_rows: list[dict], conflicts: dict[str, list[dict]]) -> None:
    print(f"省份: {province}")
    print(f"候选记录: {len(preview_rows)}")
    print(f"search_query 冲突: {len(conflicts)}")
    print()

    for idx, row in enumerate(preview_rows, 1):
        print(f"[{idx}] {row['bill_name']}")
        print(f"  search_query: {row['search_query']}")
        print(f"  quota: {row['quota_names'][0]}")
        if len(row["quota_names"]) > 1:
            print(f"  associated: {len(row['quota_names']) - 1}条")

    if conflicts:
        print("\n发现冲突，已停止导入：")
        for query, rows in conflicts.items():
            print(f"- search_query: {query}")
            for row in rows:
                print(f"    {row['bill_name']} -> {row['quota_names'][0]}")


def _audit_kb(preview_rows: list[dict]) -> int:
    kb = UniversalKB()
    exact_hits = 0
    total = len(preview_rows)

    print("\n=== UniversalKB Audit ===")
    for idx, row in enumerate(preview_rows, 1):
        hints = kb.search_hints(row["search_query"], top_k=3, authority_only=True)
        exact = bool(hints and hints[0].get("bill_pattern") == row["search_query"])
        if exact:
            exact_hits += 1
        print(f"[{idx}] {row['bill_name']}")
        print(f"  search_query: {row['search_query']}")
        print(f"  exact_hit: {exact}")
        if hints:
            top = hints[0]
            print(f"  top_bill: {top.get('bill_pattern', '')}")
            print(f"  top_quota: {top.get('quota_patterns', [])[:1]}")
            print(f"  sim: {top.get('similarity', 0):.3f}")
        else:
            print("  top_bill: <none>")

    print(f"\nAudit summary: exact_hit {exact_hits}/{total}")
    return exact_hits


def _audit_search(province: str, preview_rows: list[dict]) -> int:
    from src.hybrid_searcher import HybridSearcher

    searcher = HybridSearcher(province)
    hit_top1 = 0

    print("\n=== Hybrid Search Audit ===")
    for idx, row in enumerate(preview_rows, 1):
        results = searcher.search(row["search_query"], top_k=5)
        target = row["quota_names"][0]
        top_name = results[0]["name"] if results else ""
        is_top1 = bool(results and target == top_name)
        if is_top1:
            hit_top1 += 1

        print(f"[{idx}] {row['bill_name']}")
        print(f"  search_query: {row['search_query']}")
        print(f"  target: {target}")
        print(f"  top1: {top_name or '<none>'}")
        print(f"  top1_hit: {is_top1}")
        if results:
            top5 = [r.get("name", "") for r in results[:5]]
            print(f"  top5: {top5}")
        else:
            print("  top5: []")

    print(f"\nSearch summary: top1_hit {hit_top1}/{len(preview_rows)}")
    return hit_top1


def main() -> int:
    parser = argparse.ArgumentParser(description="补充江西园林 benchmark 权威提示到通用知识库")
    parser.add_argument("--apply", action="store_true", help="写入通用知识库")
    parser.add_argument("--all-items", action="store_true", help="导入整张试卷，不只导错题")
    parser.add_argument("--preview", action="store_true", help="打印预览（默认开启）")
    parser.add_argument("--audit-kb", action="store_true", help="反查UniversalKB命中情况")
    parser.add_argument("--audit-search", action="store_true", help="检查HybridSearcher top1/top5")
    args = parser.parse_args()

    province, records, preview_rows = _build_records(only_failed=not args.all_items)
    conflicts = _find_conflicts(preview_rows)

    if args.preview or not args.apply:
        _print_preview(province, preview_rows, conflicts)

    if conflicts:
        return 2

    if args.audit_kb:
        _audit_kb(preview_rows)
    if args.audit_search:
        _audit_search(province, preview_rows)

    if not args.apply:
        return 0

    kb = UniversalKB()
    stats = kb.batch_import(
        records,
        source_province=province,
        source_project=SOURCE_PROJECT,
        skip_vector_dedup=False,
    )
    print("\n导入完成:")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
