import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

from app.api.results import _build_feedback_payload


def test_build_feedback_payload_for_correction_keeps_trace_and_candidates():
    match_result = SimpleNamespace(
        bill_name="??????",
        bill_description="????????",
        bill_unit="kg",
        specialty="C6",
        match_source="search",
        confidence=88,
        review_status="corrected",
        quotas=[{"quota_id": "C6-1-1", "name": "????", "reasoning": {"logic_score": 0.8}}],
        corrected_quotas=None,
        alternatives=[{"quota_id": "C6-1-2", "reasoning": {"logic_score": 0.6}}],
        trace={
            "path": ["search_select"],
            "final_source": "search",
            "final_confidence": 88,
            "steps": [
                {
                    "stage": "agent_llm",
                    "selected_reasoning": {"logic_score": 0.8},
                    "candidates": [{"quota_id": "C6-1-1"}, {"quota_id": "C6-1-2"}],
                    "reasoning_engaged": True,
                    "reasoning_conflicts": ["???????: ????? / ?????"],
                    "reasoning_decision": {"is_ambiguous": True, "reason": "small_score_gap"},
                    "reasoning_compare_points": ["????????????"],
                    "query_route": {"route": "installation_spec"},
                    "batch_context": {"system_hint": "电气", "neighbor_system_hint": "电气"},
                },
                {
                    "stage": "final_validate",
                    "final_validation": {"status": "manual_review", "issues": [{"type": "unit_conflict"}]},
                    "final_review_correction": {"quota_id": "C6-1-8", "quota_name": "??????"},
                }
            ],
        },
    )

    payload = _build_feedback_payload(
        match_result,
        action="correct",
        review_note="???????????",
        corrected_quotas=[{"quota_id": "C6-1-9", "name": "?????"}],
    )

    assert payload["action"] == "correct"
    assert payload["selected_quotas"][0]["quota_id"] == "C6-1-9"
    assert payload["original_quotas"][0]["reasoning"]["logic_score"] == 0.8
    assert payload["trace"]["steps"][0]["candidates"][1]["quota_id"] == "C6-1-2"
    assert payload["trace"]["steps"][0]["batch_context"]["neighbor_system_hint"] == "电气"
    assert payload["final_validation"]["status"] == "manual_review"
    assert payload["final_review_correction"]["quota_id"] == "C6-1-8"
    assert payload["reasoning_summary"]["engaged"] is True
    assert payload["reasoning_summary"]["decision"]["reason"] == "small_score_gap"
    assert payload["query_route"]["route"] == "installation_spec"
    assert payload["batch_context"]["neighbor_system_hint"] == "电气"
