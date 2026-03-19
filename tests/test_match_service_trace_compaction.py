import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

from app.services.match_service import _compact_trace


def test_compact_trace_keeps_reasoning_and_final_validation_fields():
    trace = _compact_trace({
        "path": ["search_select", "agent_llm", "final_validate"],
        "final_source": "agent",
        "final_confidence": 83,
        "steps": [
            {
                "stage": "agent_llm",
                "reasoning_engaged": True,
                "reasoning_conflicts": ["材质冲突: 镀锌钢管 / 不锈钢管"],
                "reasoning_decision": {"is_ambiguous": True, "reason": "small_score_gap"},
                "reasoning_compare_points": ["优先核对材质是否一致"],
                "query_route": {"route": "installation_spec", "spec_count": 2},
                "batch_context": {"system_hint": "电气", "neighbor_system_hint": "电气"},
                "ignored_key": "should_drop",
            },
            {
                "stage": "final_validate",
                "final_validation": {"status": "manual_review", "issues": [{"type": "unit_conflict"}]},
                "final_review_correction": {"quota_id": "Q2", "quota_name": "镀锌钢管安装"},
            },
        ],
    })

    assert trace["steps"][0]["reasoning_engaged"] is True
    assert trace["steps"][0]["reasoning_decision"]["reason"] == "small_score_gap"
    assert trace["steps"][0]["query_route"]["route"] == "installation_spec"
    assert trace["steps"][0]["batch_context"]["neighbor_system_hint"] == "电气"
    assert "ignored_key" not in trace["steps"][0]
    assert trace["steps"][1]["final_validation"]["status"] == "manual_review"
    assert trace["steps"][1]["final_review_correction"]["quota_id"] == "Q2"
