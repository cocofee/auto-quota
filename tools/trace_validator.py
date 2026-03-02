# -*- coding: utf-8 -*-
"""追踪一条配管case的完整validator流程"""
import sys
import os
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
warnings.filterwarnings('ignore')

from loguru import logger
logger.remove()

try:
    from src.match_engine import init_search_components
    from src.text_parser import TextParser
    from src.query_builder import build_quota_query
    from src.reranker import Reranker

    province = '宁夏安装工程计价定额(2019)'
    print("初始化搜索引擎...", flush=True)
    searcher, validator = init_search_components(resolved_province=province)
    reranker = Reranker()
    parser = TextParser()

    name = '配管'
    desc = '配管 规格:SC20 配置形式:暗配 含预埋管线时可能发生的开槽、补槽、开洞、补洞、防火封堵等所有内容'
    full_query = f'{name} {desc}'
    params = parser.parse(full_query)
    search_query = build_quota_query(parser, name, desc, specialty='C4', bill_params=params)

    print(f'search_query: {search_query}', flush=True)
    print(f'bill_params: {dict((k,v) for k,v in params.items() if v)}', flush=True)
    print(flush=True)

    # 搜索
    candidates = searcher.search(search_query, top_k=20, books=None)
    print(f'=== BM25返回 {len(candidates)} 条 ===', flush=True)
    for i, c in enumerate(candidates[:10]):
        dn = c.get('dn', '?')
        hs = c.get('hybrid_score', 0)
        print(f'  [{i}] {c["quota_id"]:12s} | dn={str(dn):>4s} | hs={hs:.4f} | {c["name"][:65]}', flush=True)
    print(flush=True)

    # Rerank
    candidates = reranker.rerank(search_query, candidates)
    print(f'=== Reranker后排序 ===', flush=True)
    for i, c in enumerate(candidates[:10]):
        dn = c.get('dn', '?')
        rr = c.get('rerank_score', 0)
        print(f'  [{i}] {c["quota_id"]:12s} | dn={str(dn):>4s} | rr={rr:.4f} | {c["name"][:65]}', flush=True)
    print(flush=True)

    # Validator
    validated = validator.validate_candidates(full_query, candidates, supplement_query=search_query)
    print(f'=== Validator后排序 ===', flush=True)
    for i, c in enumerate(validated[:10]):
        dn = c.get('dn', '?')
        pm = c.get('param_match', '?')
        ps = c.get('param_score', 0)
        rr = c.get('rerank_score', 0)
        fusion = ps * 0.8 + rr * 0.2
        print(f'  [{i}] {c["quota_id"]:12s} | dn={str(dn):>4s} | pm={pm!s:5s} | ps={ps:.3f} | rr={rr:.3f} | fusion={fusion:.3f} | {c.get("param_detail", "")[:50]}', flush=True)

except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"ERROR: {e}", flush=True)
