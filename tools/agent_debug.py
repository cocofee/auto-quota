"""
Agent单条调试工具
用法: python tools/agent_debug.py "清单名称" "特征描述"
功能: 对单条清单运行Agent匹配，实时显示搜索过程和大模型分析结果

示例:
  python tools/agent_debug.py "镀锌钢管管道安装" "DN150 沟槽连接"
  python tools/agent_debug.py "矩形风管制作安装" "碳钢板 大边长1000mm 咬口连接"
"""

import sys
import io
import time
import json
import argparse

# 修复Windows终端GBK编码问题（支持中文和Unicode特殊字符）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import config
from src.hybrid_searcher import HybridSearcher
from src.param_validator import ParamValidator
from src.text_parser import parser as text_parser
from src.specialty_classifier import classify as classify_specialty
from src.reranker import Reranker
from src.agent_matcher import AgentMatcher


def _resolve_runtime_province(name: str = None) -> str:
    """Resolve province for this run without mutating process-global runtime state."""
    province = config.resolve_province(name, interactive=False)
    return province


def _normalize_fallbacks(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    elif isinstance(value, str):
        items = [value]
    else:
        items = []

    cleaned = []
    seen = set()
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def debug_single(bill_name: str, bill_desc: str = "",
                 llm_type: str = None, top_k: int = 20,
                 no_llm: bool = False, province: str = None):
    """对单条清单运行Agent调试"""
    province = _resolve_runtime_province(province)
    print(f"\n{'='*70}")
    print(f"Agent调试: {bill_name}")
    if bill_desc:
        print(f"特征描述: {bill_desc}")
    print(f"省份: {province}")
    print(f"{'='*70}")

    full_text = f"{bill_name} {bill_desc}".strip()

    # 第1步：参数提取
    print(f"\n[1] 参数提取...")
    params = text_parser.parse(full_text)
    search_query = text_parser.build_quota_query(bill_name, bill_desc)
    print(f"  提取参数: {json.dumps(params, ensure_ascii=False)}")
    print(f"  搜索词: {search_query}")

    # 第2步：专业分类
    print(f"\n[2] 专业分类...")
    classification = classify_specialty(bill_name, bill_desc)
    primary = classification.get("primary", "未知")
    primary_name = classification.get("primary_name", "")
    fallbacks = _normalize_fallbacks(classification.get("fallbacks", []))
    if primary:
        fallbacks = [b for b in fallbacks if b != primary]
    print(f"  主专业: {primary} {primary_name}")
    if fallbacks:
        print(f"  借用专业: {fallbacks}")

    # 第3步：搜索
    print(f"\n[3] 搜索定额库...")
    searcher = HybridSearcher(province=province)
    books = [primary] + fallbacks if primary else None
    candidates = searcher.search(search_query, top_k=top_k, books=books)
    print(f"  搜索到 {len(candidates)} 条候选")

    # 第4步：Reranker重排
    if candidates:
        print(f"\n[4] Reranker重排...")
        reranker = Reranker()
        candidates = reranker.rerank(search_query, candidates)
        print(f"  重排后 Top-3:")
        for i, c in enumerate(candidates[:3], 1):
            print(f"    {i}. [{c['quota_id']}] {c['name']} (rerank:{c.get('rerank_score', 0):.3f})")

    # 第5步：参数验证
    if candidates:
        print(f"\n[5] 参数验证...")
        validator = ParamValidator()
        candidates = validator.validate_candidates(
            full_text, candidates, supplement_query=search_query)
        matched = [c for c in candidates if c.get("param_match", True)]
        print(f"  参数匹配: {len(matched)}/{len(candidates)} 条")
        print(f"  验证后 Top-5:")
        for i, c in enumerate(candidates[:5], 1):
            pm = "✓" if c.get("param_match", True) else "✗"
            ps = c.get("param_score", 0)
            detail = c.get("param_detail", "")[:60]
            print(f"    {i}. {pm}({ps:.0%}) [{c['quota_id']}] {c['name']}")
            if detail:
                print(f"       {detail}")

    # 第6步：查经验库参考
    reference_cases = None
    try:
        from src.experience_db import ExperienceDB
        exp_db = ExperienceDB(province=province)
        reference_cases = exp_db.get_reference_cases(full_text, top_k=3, province=province)
        if reference_cases:
            print(f"\n[6] 经验库参考案例:")
            for case in reference_cases:
                quotas_str = ", ".join(case.get("quotas", []))
                print(f"  \"{case.get('bill', '')[:40]}\" → {quotas_str}")
        else:
            print(f"\n[6] 经验库: 无相似案例")
    except Exception as e:
        print(f"\n[6] 经验库: 不可用 ({e})")

    # 第7步：Agent大模型分析（可跳过）
    if not candidates:
        print(f"\n[7] 无候选，跳过Agent分析")
        return

    if no_llm:
        # 不调大模型，只输出候选列表供人工分析
        print(f"\n[7] 跳过大模型（--no-llm模式），候选列表已输出")
        # 输出JSON供后续分析
        output = {
            "bill_name": bill_name,
            "bill_desc": bill_desc,
            "search_query": search_query,
            "params": params,
            "specialty": primary,
            "candidates": [
                {
                    "rank": i+1,
                    "quota_id": c["quota_id"],
                    "name": c["name"],
                    "unit": c.get("unit", ""),
                    "param_match": c.get("param_match", True),
                    "param_score": round(c.get("param_score", 0), 3),
                    "param_detail": c.get("param_detail", ""),
                    "rerank_score": round(c.get("rerank_score", 0), 3),
                }
                for i, c in enumerate(candidates[:10])
            ],
            "reference_cases": reference_cases or [],
        }
        print(f"\n--- JSON候选数据 ---")
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    agent_llm = llm_type or config.AGENT_LLM
    print(f"\n[7] Agent分析（大模型: {agent_llm}）...")

    bill_item = {
        "name": bill_name,
        "description": bill_desc,
        "unit": "",
        "quantity": "",
        "specialty": primary,
        "specialty_name": primary_name,
        "specialty_fallbacks": fallbacks,
        "params": params,
    }

    agent = AgentMatcher(llm_type=agent_llm, province=province)
    start = time.time()
    result = agent.match_single(
        bill_item=bill_item,
        candidates=candidates,
        reference_cases=reference_cases,
        search_query=search_query,
    )
    elapsed = time.time() - start

    # 输出结果
    print(f"\n{'='*70}")
    print(f"匹配结果（耗时 {elapsed:.1f}秒）")
    print(f"{'='*70}")

    quotas = result.get("quotas", [])
    if quotas:
        for i, q in enumerate(quotas):
            label = "主定额" if i == 0 else f"关联{i}"
            print(f"  {label}: [{q['quota_id']}] {q['name']}")
            if q.get("reason"):
                print(f"         理由: {q['reason']}")
    else:
        print(f"  未匹配: {result.get('no_match_reason', '未知原因')}")

    print(f"  置信度: {result.get('confidence', 0)}%")
    print(f"  来源: {result.get('match_source', '')}")

    if result.get("explanation"):
        print(f"  说明: {result['explanation']}")

    # 显示备选
    alternatives = result.get("alternatives", [])
    if alternatives:
        print(f"\n  备选定额:")
        for alt in alternatives[:3]:
            print(f"    [{alt['quota_id']}] {alt['name']} ({alt.get('confidence', 0)}%)")

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent单条调试工具")
    parser.add_argument("bill_name", help="清单名称")
    parser.add_argument("bill_desc", nargs="?", default="", help="特征描述")
    parser.add_argument("--llm", default=None, help="指定Agent模型类型")
    parser.add_argument("--top-k", type=int, default=20, help="搜索候选数量")
    parser.add_argument("--no-llm", action="store_true", help="只跑检索与参数验证，不调用大模型")
    parser.add_argument("--province", default=None, help=f"省份（默认: {config.CURRENT_PROVINCE}）")
    args = parser.parse_args()

    debug_single(
        args.bill_name,
        args.bill_desc,
        llm_type=args.llm,
        top_k=args.top_k,
        no_llm=args.no_llm,
        province=args.province,
    )
