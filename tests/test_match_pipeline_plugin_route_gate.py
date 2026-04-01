from src.match_pipeline import _apply_plugin_candidate_biases, _apply_plugin_route_gate


def test_plugin_route_gate_never_prunes_candidates_even_when_strict_requested():
    candidates = [
        {"quota_id": "C10-1-1", "name": "water_pipe"},
        {"quota_id": "C4-1-1", "name": "power_box_wall"},
        {"quota_id": "C4-1-2", "name": "power_box_floor"},
        {"quota_id": "C8-1-1", "name": "municipal_power"},
    ]

    gated, meta = _apply_plugin_route_gate(
        {"plugin_hints": {"preferred_books": ["C4"], "strict_preferred_books": True}},
        candidates,
    )

    assert meta["applied"] is False
    assert meta["reason"] == "strict_preferred_books_disabled"
    assert meta["preferred_count"] == 2
    assert meta["strict_requested"] is True
    assert [item["quota_id"] for item in gated] == [item["quota_id"] for item in candidates]
    assert [item["plugin_route_book"] for item in gated] == ["C10", "C4", "C4", "C8"]


def test_plugin_route_gate_reports_zero_matches_without_pruning():
    candidates = [
        {"quota_id": "C10-1-1", "name": "water_pipe"},
        {"quota_id": "C8-1-1", "name": "municipal_power"},
    ]

    gated, meta = _apply_plugin_route_gate(
        {"plugin_hints": {"preferred_books": ["C4"], "strict_preferred_books": True}},
        candidates,
    )

    assert meta["applied"] is False
    assert meta["reason"] == "strict_preferred_books_disabled"
    assert meta["preferred_count"] == 0
    assert meta["strict_requested"] is True
    assert [item["quota_id"] for item in gated] == [item["quota_id"] for item in candidates]


def test_plugin_route_gate_defaults_to_soft_mode():
    candidates = [
        {"quota_id": "C10-1-1", "name": "water_pipe"},
        {"quota_id": "C4-1-1", "name": "power_box_wall"},
    ]

    gated, meta = _apply_plugin_route_gate(
        {"plugin_hints": {"preferred_books": ["C4"]}},
        candidates,
    )

    assert meta["applied"] is False
    assert meta["reason"] == "soft_preferred_books_only"
    assert meta["preferred_count"] == 1
    assert [item["quota_id"] for item in gated] == [item["quota_id"] for item in candidates]


def test_plugin_candidate_biases_remain_soft_signal():
    candidates = [
        {
            "quota_id": "C10-1-1",
            "name": "water_pipe",
            "param_score": 0.95,
            "rerank_score": 0.90,
        },
        {
            "quota_id": "C4-1-1",
            "name": "power_box_wall",
            "param_score": 0.20,
            "rerank_score": 0.20,
        },
    ]

    ranked = _apply_plugin_candidate_biases(
        {"plugin_hints": {"preferred_books": ["C4"]}},
        candidates,
    )

    assert ranked[0]["quota_id"] == "C10-1-1"
    assert ranked[1]["plugin_score"] > ranked[0]["plugin_score"]
