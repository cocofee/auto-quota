# -*- coding: utf-8 -*-

from src.match_core import _summarize_candidates_for_trace, summarize_candidate_reasoning
from src.match_pipeline import _build_alternatives, _build_search_result_from_candidates


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


def test_build_alternatives_attaches_reasoning():
    alternatives = _build_alternatives([_candidate()], top_n=1)

    assert len(alternatives) == 1
    assert alternatives[0]["reasoning"]["layers"]["logic"]["detail"] == "DN=100"
