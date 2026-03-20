from src.match_pipeline import _apply_plugin_candidate_biases, _apply_plugin_route_gate


def test_plugin_route_gate_prefers_matching_books_but_keeps_small_fallback():
    candidates = [
        {"quota_id": "C10-1-1", "name": "给排水管道"},
        {"quota_id": "C4-1-1", "name": "成套配电箱安装 悬挂、嵌入式"},
        {"quota_id": "C4-1-2", "name": "成套配电箱安装 落地式"},
        {"quota_id": "C8-1-1", "name": "市政配电设施"},
    ]

    gated, meta = _apply_plugin_route_gate(
        {"plugin_hints": {"preferred_books": ["C4"], "strict_preferred_books": True}},
        candidates,
    )

    assert meta["applied"] is True
    assert meta["reason"] == "preferred_books_gate"
    assert [item["quota_id"] for item in gated[:2]] == ["C4-1-1", "C4-1-2"]
    assert len(gated) == 4


def test_plugin_route_gate_skips_when_no_matching_book_candidates():
    candidates = [
        {"quota_id": "C10-1-1", "name": "给排水管道"},
        {"quota_id": "C8-1-1", "name": "市政配电设施"},
    ]

    gated, meta = _apply_plugin_route_gate(
        {"plugin_hints": {"preferred_books": ["C4"], "strict_preferred_books": True}},
        candidates,
    )

    assert meta["applied"] is False
    assert meta["reason"] == "no_matching_book_candidates"
    assert gated == candidates


def test_plugin_route_gate_defaults_to_soft_mode():
    candidates = [
        {"quota_id": "C10-1-1", "name": "给排水管道"},
        {"quota_id": "C4-1-1", "name": "成套配电箱安装 悬挂、嵌入式"},
    ]

    gated, meta = _apply_plugin_route_gate(
        {"plugin_hints": {"preferred_books": ["C4"]}},
        candidates,
    )

    assert meta["applied"] is False
    assert meta["reason"] == "soft_preferred_books_only"
    assert gated == candidates


def test_plugin_candidate_biases_remain_soft_signal():
    candidates = [
        {
            "quota_id": "C10-1-1",
            "name": "给排水管道",
            "param_score": 0.95,
            "rerank_score": 0.90,
        },
        {
            "quota_id": "C4-1-1",
            "name": "成套配电箱安装 悬挂、嵌入式",
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
