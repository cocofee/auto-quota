# -*- coding: utf-8 -*-

import pytest

from src.match_core import _summarize_candidates_for_trace, summarize_candidate_reasoning
from src.match_pipeline import _build_alternatives, _build_item_context, _build_search_result_from_candidates


def _candidate() -> dict:
    return {
        "quota_id": "C4-1-1",
        "name": "电缆桥架支架制作安装",
        "unit": "m",
        "param_match": True,
        "param_score": 0.96,
        "param_tier": 2,
        "param_detail": "参数匹配; 特征对齐0.92; 上下文对齐0.95",
        "name_bonus": 0.35,
        "rerank_score": 0.88,
        "feature_alignment_score": 0.92,
        "feature_alignment_detail": "实体:支架; 系统:电气",
        "feature_alignment_comparable_count": 2,
        "logic_score": 0.90,
        "logic_detail": "DN=100",
        "logic_comparable_count": 1,
        "logic_exact_primary_match": True,
        "context_alignment_score": 0.95,
        "context_alignment_detail": "上下文系统:电气; 上下文提示:1/1",
        "context_alignment_comparable_count": 2,
    }


def test_summarize_candidate_reasoning_includes_structured_layers():
    reasoning = summarize_candidate_reasoning(_candidate())

    assert reasoning["param_match"] is True
    assert reasoning["param_score"] == 0.96
    assert reasoning["layers"]["feature"]["score"] == 0.92
    assert reasoning["layers"]["logic"]["exact_primary_match"] is True
    assert reasoning["layers"]["context"]["score"] == 0.95


def test_summarize_candidates_for_trace_includes_reasoning():
    summary = _summarize_candidates_for_trace([_candidate()], top_n=1)

    assert len(summary) == 1
    assert summary[0]["quota_id"] == "C4-1-1"
    assert summary[0]["reasoning"]["layers"]["context"]["score"] == 0.95


def test_build_search_result_attaches_reasoning_to_selected_quota_and_trace():
    result = _build_search_result_from_candidates(
        {
            "name": "支架",
            "description": "",
            "query_route": {"route": "installation_spec"},
            "classification": {
                "primary": "C4",
                "fallbacks": ["C10"],
                "candidate_books": ["C4", "C10"],
                "search_books": ["C4", "C10"],
                "hard_book_constraints": ["C4"],
                "route_mode": "strict",
            },
            "context_prior": {
                "context_hints": ["桥架"],
                "system_hint": "电气",
                "batch_context": {
                    "project_system_hint": "电气",
                    "section_system_hint": "电气",
                    "neighbor_system_hint": "电气",
                    "batch_size": 6,
                },
            },
        },
        [
            _candidate(),
            {
                "quota_id": "C10-1-1",
                "name": "管道支架制作安装",
                "param_match": True,
                "param_score": 0.80,
                "param_detail": "回退候选",
                "rerank_score": 0.30,
            },
        ],
    )

    assert result["quotas"][0]["reasoning"]["layers"]["feature"]["score"] == 0.92
    assert result["reasoning_decision"]["route"] == "installation_spec"
    assert result["needs_reasoning"] is False
    assert result["require_final_review"] is False
    trace_step = result["trace"]["steps"][-1]
    assert trace_step["stage"] == "search_select"
    assert trace_step["selected_reasoning"]["layers"]["context"]["score"] == 0.95
    assert "arbitration" in trace_step
    assert trace_step["reasoning_decision"]["route"] == "installation_spec"
    assert trace_step["batch_context"]["neighbor_system_hint"] == "电气"
    assert trace_step["parser"]["search_query"]
    assert trace_step["router"]["query_route"]["route"] == "installation_spec"
    assert trace_step["router"]["advisory_owner"] == ""
    assert trace_step["router"]["effective_owner"] == "specialty_classifier"
    assert trace_step["router"]["classification"]["primary"] == "C4"
    assert trace_step["router"]["classification"]["search_books"] == ["C4", "C10"]
    assert trace_step["router"]["classification"]["hard_book_constraints"] == ["C4"]
    assert trace_step["router"]["classification"]["hard_search_books"] == ["C4"]
    assert trace_step["router"]["classification"]["advisory_search_books"] == ["C10"]
    assert trace_step["retriever"]["candidate_count"] == 2
    assert trace_step["retriever"]["kb_hit"] is False
    assert trace_step["retriever"]["scope_owner"] == "specialty_classifier"
    assert trace_step["retriever"]["escape_owner"] == ""
    assert trace_step["ranker"]["selected_quota"] == "C4-1-1"
    assert trace_step["ranker"]["score_gap"] >= 0.0
    assert trace_step["ranker"]["selected_rank_breakdown"]["rank_score"] == trace_step["ranker"]["selected_rank_score"]
    assert trace_step["ranker"]["second_rank_breakdown"]["rank_score"] == trace_step["ranker"]["second_rank_score"]
    assert trace_step["ranker"]["decision_owner"] == "pre_ltr_seed"
    assert trace_step["ranker"]["top1_flip_count"] == 0
    assert result["candidate_snapshots"][0]["rank_score_breakdown"]["rank_score"] == pytest.approx(result["candidate_snapshots"][0]["rank_score"])
    assert "stage_priority" in result["candidate_snapshots"][0]["rank_score_breakdown"]


def test_build_alternatives_attaches_reasoning():
    alternatives = _build_alternatives([_candidate()], top_n=1)

    assert len(alternatives) == 1
    assert alternatives[0]["reasoning"]["layers"]["logic"]["detail"] == "DN=100"


def test_build_search_result_marks_kb_hit_from_exact_kb_candidate():
    result = _build_search_result_from_candidates(
        {
            "name": "KB bill",
            "description": "",
            "query_route": {"route": "installation_spec"},
            "classification": {
                "primary": "C4",
                "search_books": ["C4"],
            },
        },
        [
            {
                "quota_id": "Q-KB-1",
                "name": "KB exact quota",
                "unit": "m",
                "param_match": True,
                "param_score": 0.90,
                "param_detail": "kb",
                "rerank_score": 0.60,
                "match_source": "kb_injected_exact",
                "knowledge_prior_sources": ["universal_kb"],
                "knowledge_prior_score": 1.0,
            }
        ],
    )

    trace_step = result["trace"]["steps"][-1]
    assert trace_step["retriever"]["kb_hit"] is True


def test_build_item_context_attaches_primary_query_profile():
    ctx = _build_item_context(
        {
            "name": "钢塑复合管",
            "description": "DN50 螺纹连接 含穿非混凝土构件的套管制作及安装",
            "specialty": "C10",
        }
    )

    profile = ctx["canonical_query"]["primary_query_profile"]
    assert profile["primary_subject"] == "钢塑复合管"
    assert "DN50" in profile["key_specs"]
    assert profile["noise_marker"] == "含"
    assert ctx["query_route"]["primary_subject"] == "钢塑复合管"


def test_build_search_result_appends_supplemental_quotas_after_main_quota():
    result = _build_search_result_from_candidates(
        {
            "name": "管道支架",
            "description": "除锈后刷防锈漆二道,再刷灰色调和漆二道",
            "query_route": {"route": "installation_spec"},
            "_supplemental_quotas": [
                {
                    "quota_id": "C12-1-1",
                    "name": "手工除锈 一般钢结构 轻锈",
                    "unit": "kg",
                    "reason": "附加定额:手工除锈 一般钢结构 轻锈",
                    "is_supplemental": True,
                },
                {
                    "quota_id": "C12-1-2",
                    "name": "一般钢结构 防锈漆 第一遍",
                    "unit": "kg",
                    "reason": "附加定额:一般钢结构 防锈漆 第一遍",
                    "is_supplemental": True,
                },
            ],
        },
        [_candidate()],
    )

    assert result["quotas"][0]["quota_id"] == "C4-1-1"
    assert result["quotas"][1]["quota_id"] == "C12-1-1"
    assert result["quotas"][2]["quota_id"] == "C12-1-2"


def test_build_search_result_records_distribution_box_explicit_advisory_without_reordering():
    result = _build_search_result_from_candidates(
        {
            "name": "\u914d\u7535\u7bb1",
            "description": "\u5b89\u88c5\u65b9\u5f0f:\u660e\u88c5 \u89c4\u683c:600*900*220 8\u56de\u8def",
            "query_route": {"route": "installation_spec"},
        },
        [
            {
                "quota_id": "AH-J1",
                "name": "\u63a5\u7ebf\u7bb1\u660e\u88c5 \u534a\u5468\u957f(mm\u4ee5\u5185) 1500",
                "unit": "\u4e2a",
                "param_match": True,
                "param_score": 0.99,
                "param_detail": "\u9ad8\u5206\u5e72\u6270\u9879",
                "rerank_score": 0.99,
            },
            {
                "quota_id": "AH-W1",
                "name": "\u76d8\u3001\u67dc\u3001\u7bb1\u3001\u677f\u914d\u7ebf \u5bfc\u7ebf\u622a\u9762(mm2\u4ee5\u5185) 25",
                "unit": "m",
                "param_match": True,
                "param_score": 0.98,
                "param_detail": "\u9ad8\u5206\u5e72\u6270\u9879",
                "rerank_score": 0.98,
            },
            {
                "quota_id": "AH-B1",
                "name": "\u6210\u5957\u914d\u7535\u7bb1\u5b89\u88c5 \u60ac\u6302\u3001\u5d4c\u5165\u5f0f \u534a\u5468\u957f1.5m \u89c4\u683c(\u56de\u8def\u4ee5\u5185) 8",
                "unit": "\u53f0",
                "param_match": True,
                "param_score": 0.72,
                "param_detail": "\u534a\u5468\u957f1500=1500 \u7cbe\u786e\u5339\u914d; \u56de\u8def8=8 \u7cbe\u786e\u5339\u914d",
                "rerank_score": 0.52,
            },
        ],
    )

    assert result["quotas"][0]["quota_id"] == "AH-J1"
    assert result["explicit_override"]["applied"] is False
    assert result["explicit_override"]["advisory_applied"] is True
    assert result["explicit_override"]["recommended_quota_id"] == "AH-B1"
    assert result["trace"]["steps"][-1]["selected_quota"] == "AH-J1"
    assert result["trace"]["steps"][-1]["explicit_override"]["recommended_quota_id"] == "AH-B1"


def test_build_search_result_keeps_ltr_top1_when_no_arbiter_or_explicit_advisory(monkeypatch):
    def _fake_rerank(item, candidates, context):
        return candidates, {
            "post_ltr_top1_id": "KEEP-1",
            "post_cgr_top1_id": "KEEP-1",
        }

    def _fake_arbiter(item, candidates, route_profile=None):
        return candidates, {
            "applied": False,
            "advisory_applied": False,
            "reason": "route_disabled",
            "recommended_quota_id": "",
        }

    monkeypatch.setattr("src.match_pipeline.rerank_candidates_with_ltr", _fake_rerank)
    monkeypatch.setattr("src.match_pipeline.arbitrate_candidates", _fake_arbiter)

    result = _build_search_result_from_candidates(
        {
            "name": "风管",
            "description": "镀锌薄钢板风管",
            "query_route": {"route": "semantic_description"},
        },
        [
            {
                "quota_id": "KEEP-1",
                "name": "镀锌薄钢板风管 规格1",
                "unit": "m2",
                "param_match": True,
                "param_score": 0.72,
                "param_tier": 2,
                "param_detail": "LTR已选中该项",
                "rerank_score": 0.80,
                "_rank_score_source": "manual",
                "manual_structured_score": 0.20,
            },
            {
                "quota_id": "FLIP-2",
                "name": "镀锌薄钢板风管 规格2",
                "unit": "m2",
                "param_match": True,
                "param_score": 0.72,
                "param_tier": 2,
                "param_detail": "如果二次重排会错误翻转到这里",
                "rerank_score": 0.79,
                "_rank_score_source": "manual",
                "manual_structured_score": 0.95,
            },
        ],
    )

    assert result["post_ltr_top1_id"] == "KEEP-1"
    assert result["post_arbiter_top1_id"] == "KEEP-1"
    assert result["post_explicit_top1_id"] == "KEEP-1"
    assert result["quotas"][0]["quota_id"] == "KEEP-1"


def test_build_search_result_records_ltr_as_rank_decision_owner(monkeypatch):
    def _fake_rerank(item, candidates, context):
        flipped = [dict(candidates[1]), dict(candidates[0])]
        return flipped, {
            "post_ltr_top1_id": "FLIP-2",
            "post_cgr_top1_id": "FLIP-2",
        }

    def _fake_arbiter(item, candidates, route_profile=None):
        return candidates, {
            "applied": False,
            "advisory_applied": False,
            "reason": "no_change",
            "recommended_quota_id": "",
        }

    monkeypatch.setattr("src.match_pipeline.rerank_candidates_with_ltr", _fake_rerank)
    monkeypatch.setattr("src.match_pipeline.arbitrate_candidates", _fake_arbiter)

    result = _build_search_result_from_candidates(
        {
            "name": "风管",
            "description": "镀锌薄钢板风管",
            "query_route": {"route": "semantic_description"},
        },
        [
            {
                "quota_id": "KEEP-1",
                "name": "镀锌薄钢板风管 规格1",
                "unit": "m2",
                "param_match": True,
                "param_score": 0.80,
                "param_tier": 2,
                "param_detail": "原始top1",
                "rerank_score": 0.80,
            },
            {
                "quota_id": "FLIP-2",
                "name": "镀锌薄钢板风管 规格2",
                "unit": "m2",
                "param_match": True,
                "param_score": 0.79,
                "param_tier": 2,
                "param_detail": "LTR翻转为top1",
                "rerank_score": 0.79,
            },
        ],
    )

    ranker = result["trace"]["steps"][-1]["ranker"]
    assert result["quotas"][0]["quota_id"] == "FLIP-2"
    assert result["rank_decision_owner"] == "ltr"
    assert result["rank_top1_flip_count"] == 1
    assert ranker["decision_owner"] == "ltr"
    assert ranker["rank_timeline_changes"][0]["stage"] == "ltr"


def test_build_search_result_marks_unified_plan_as_router_owner_and_escape_as_retriever_owner():
    result = _build_search_result_from_candidates(
        {
            "name": "墙面装饰板",
            "description": "木饰面",
            "query_route": {"route": "balanced"},
            "plugin_hints": {"preferred_books": ["A"], "source": "manual_curated"},
            "unified_plan": {
                "primary_book": "A",
                "preferred_books": ["A"],
                "route_mode": "moderate",
                "search_aliases": ["墙面装饰板"],
            },
            "classification": {
                "primary": "A",
                "fallbacks": [],
                "candidate_books": ["A"],
                "search_books": ["A"],
                "hard_book_constraints": [],
                "route_mode": "moderate",
                "reason": "unified_plan:province_plugin",
                "retrieval_resolution": {
                    "calls": [
                        {
                            "target": "main",
                            "stage": "primary_skipped",
                            "requested_books": ["A"],
                            "resolved_books": [],
                            "open_search": True,
                        },
                        {
                            "target": "main",
                            "stage": "escape",
                            "requested_books": [],
                            "resolved_books": [],
                            "open_search": True,
                        },
                    ]
                },
            },
        },
        [
            {
                "quota_id": "03-3-7-15",
                "name": "衬微晶板",
                "unit": "m2",
                "param_match": True,
                "param_score": 0.95,
                "param_detail": "ok",
                "rerank_score": 0.88,
            }
        ],
    )

    trace_step = result["trace"]["steps"][-1]
    assert trace_step["router"]["advisory_owner"] == "unified_plan"
    assert trace_step["router"]["effective_owner"] == "unified_plan"
    assert trace_step["retriever"]["scope_owner"] == "retriever_main_escape"
    assert trace_step["retriever"]["escape_owner"] == "retriever_main_escape"
    assert trace_step["retriever"]["used_open_search"] is True
