import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

from app.services.match_service import _compact_trace


def test_compact_trace_keeps_reasoning_final_validation_and_review_rejection():
    trace = _compact_trace({
        "path": ["experience_review_rejected", "agent_llm", "final_validate"],
        "final_source": "agent",
        "final_confidence": 83,
        "steps": [
            {
                "stage": "experience_review_rejected",
                "error_type": "category_mismatch",
                "error_reason": "review rejected experience direct hit",
                "experience_source": "experience_exact",
                "quota_id": "Q-EXP-1",
            },
            {
                "stage": "agent_llm",
                "reasoning_engaged": True,
                "reasoning_conflicts": ["material_conflict"],
                "reasoning_decision": {"is_ambiguous": True, "reason": "small_score_gap"},
                "parser": {"entity": "pipe", "search_query": "pipe DN100"},
                "router": {"primary_book": "C10", "query_route": {"route": "installation_spec"}},
                "retriever": {"candidate_count": 3, "authority_hit": True, "kb_hit": False},
                "ranker": {"selected_quota": "Q1", "score_gap": 0.12},
                "ltr_rerank": {"post_ltr_top1_id": "Q1", "ltr_guard": {"action": "blocked"}},
                "reasoning_compare_points": ["check material first"],
                "query_route": {"route": "installation_spec", "spec_count": 2},
                "batch_context": {"system_hint": "electric", "neighbor_system_hint": "electric"},
                "ignored_key": "should_drop",
            },
            {
                "stage": "final_validate",
                "final_validation": {"status": "manual_review", "issues": [{"type": "unit_conflict"}]},
                "final_review_correction": {"quota_id": "Q2", "quota_name": "pipe install"},
            },
        ],
    })

    assert trace["steps"][0]["stage"] == "experience_review_rejected"
    assert trace["steps"][0]["error_type"] == "category_mismatch"
    assert trace["steps"][0]["experience_source"] == "experience_exact"
    assert trace["steps"][0]["quota_id"] == "Q-EXP-1"
    assert trace["steps"][1]["reasoning_engaged"] is True
    assert trace["steps"][1]["reasoning_decision"]["reason"] == "small_score_gap"
    assert trace["steps"][1]["parser"]["entity"] == "pipe"
    assert trace["steps"][1]["router"]["primary_book"] == "C10"
    assert trace["steps"][1]["retriever"]["authority_hit"] is True
    assert trace["steps"][1]["retriever"]["kb_hit"] is False
    assert trace["steps"][1]["ranker"]["selected_quota"] == "Q1"
    assert trace["steps"][1]["ltr_rerank"]["ltr_guard"]["action"] == "blocked"
    assert trace["steps"][1]["query_route"]["route"] == "installation_spec"
    assert trace["steps"][1]["batch_context"]["neighbor_system_hint"] == "electric"
    assert "ignored_key" not in trace["steps"][1]
    assert trace["steps"][2]["final_validation"]["status"] == "manual_review"
    assert trace["steps"][2]["final_review_correction"]["quota_id"] == "Q2"
