import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

from app.services.match_service import _compact_quota_items, _compact_trace


def test_compact_quota_items_keeps_reasoning():
    raw = [{
        "quota_id": "C10-1-1",
        "name": "??????",
        "unit": "m",
        "param_score": 0.98,
        "rerank_score": 0.91,
        "source": "search",
        "reason": "DN100????",
        "reasoning": {"param_score": 1.0, "detail": "dn=100"},
        "db_id": 12,
    }]

    items = _compact_quota_items(raw)

    assert items[0]["reason"] == "DN100????"
    assert items[0]["reasoning"]["param_score"] == 1.0
    assert items[0]["db_id"] == 12


def test_compact_trace_keeps_candidate_snapshot():
    trace = {
        "path": ["search_select", "search_mode_final"],
        "final_source": "search",
        "final_confidence": 93,
        "steps": [
            {
                "stage": "search_select",
                "selected_quota": "C10-1-1",
                "selected_reasoning": {"logic_score": 1.0},
                "candidates": [{"quota_id": "C10-1-1", "reasoning": {"logic_score": 1.0}}],
                "extra": "drop",
            }
        ],
    }

    compact = _compact_trace(trace)

    assert compact["final_source"] == "search"
    assert compact["steps"][0]["selected_reasoning"]["logic_score"] == 1.0
    assert compact["steps"][0]["candidates"][0]["quota_id"] == "C10-1-1"
    assert "extra" not in compact["steps"][0]
