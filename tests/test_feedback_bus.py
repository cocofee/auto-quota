from unittest.mock import MagicMock, patch

import config

from src.feedback_bus import (
    emit_feedback_event,
    get_feedback_bus,
    lookup_consistency_hint,
    remember_consistency_hint,
    remember_cross_province_hints,
    reset_feedback_bus,
)


def test_feedback_bias_consumes_ranking_feedback_events(tmp_path, monkeypatch):
    from src.hybrid_searcher import HybridSearcher

    reset_feedback_bus(tmp_path / "feedback_bus.db")
    monkeypatch.setattr(config, "HYBRID_FEEDBACK_ADAPTIVE_BIAS", True, raising=False)
    monkeypatch.setattr(config, "HYBRID_FEEDBACK_MIN_SAMPLES", 20, raising=False)
    monkeypatch.setattr(config, "HYBRID_FEEDBACK_BIAS_MAX", 0.08, raising=False)
    monkeypatch.setattr(config, "HYBRID_FEEDBACK_BIAS_REFRESH_SEC", 0, raising=False)

    for _ in range(12):
        emit_feedback_event(
            "ranking_feedback",
            signal="correct",
            province="test-province",
            bill_text="pipe DN25 galvanized steel installation",
            item_name="pipe",
        )
        emit_feedback_event(
            "ranking_feedback",
            signal="confirm",
            province="test-province",
            bill_text="distribution box installation with foundation and commissioning",
            item_name="box",
        )

    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher.province = "test-province"
    searcher._feedback_bias_ts = 0.0
    searcher._feedback_bias_value = 0.0
    searcher._experience_db = None

    bias = HybridSearcher._get_feedback_bias(searcher)
    assert bias > 0


def test_consistency_hint_reused_across_files(tmp_path):
    from src.match_engine import _inject_consistency_hint, _update_consistency_memory

    reset_feedback_bus(tmp_path / "feedback_bus.db")

    memory = {}
    _update_consistency_memory(
        memory,
        {"name": "\u9600\u95e8", "specialty": "C10"},
        {
            "confidence": 92,
            "quotas": [{"name": "\u95f8\u9600"}],
            "match_source": "search",
        },
        province="beijing",
    )

    assert lookup_consistency_hint("beijing", "\u9600\u95e8", "C10") == "\u95f8\u9600"

    next_item = {"name": "\u9600\u95e8", "specialty": "C10", "_is_ambiguous_short": True}
    _inject_consistency_hint(next_item, {}, province="beijing")
    assert next_item["_context_hints"] == ["\u95f8\u9600"]


def test_consistency_hint_is_scoped_by_province(tmp_path):
    from src.match_engine import _inject_consistency_hint, _update_consistency_memory

    reset_feedback_bus(tmp_path / "feedback_bus.db")

    _update_consistency_memory(
        {},
        {"name": "\u9600\u95e8", "specialty": "C10"},
        {
            "confidence": 92,
            "quotas": [{"name": "\u95f8\u9600"}],
            "match_source": "search",
        },
        province="beijing",
    )

    assert lookup_consistency_hint("guangdong", "\u9600\u95e8", "C10") == ""
    next_item = {"name": "\u9600\u95e8", "specialty": "C10", "_is_ambiguous_short": True}
    _inject_consistency_hint(next_item, {}, province="guangdong")
    assert "_context_hints" not in next_item


def test_consistency_hint_lookup_is_best_effort(monkeypatch):
    monkeypatch.setattr(
        "src.feedback_bus.get_feedback_bus",
        lambda: (_ for _ in ()).throw(RuntimeError("locked")),
    )

    assert remember_consistency_hint(
        province="beijing",
        item_name="\u9600\u95e8",
        specialty="C10",
        family_hint="\u95f8\u9600",
    ) == 0
    assert lookup_consistency_hint("beijing", "\u9600\u95e8", "C10") == ""


def test_prepare_candidates_reuses_persisted_cross_province_hints(tmp_path):
    from src.match_core import _prepare_candidates_from_prepared

    reset_feedback_bus(tmp_path / "feedback_bus.db")
    remember_cross_province_hints(
        item_name="pipe",
        specialty="C10",
        province="guangdong2024",
        bill_text="pipe DN25",
        hints=["pipe install galvanized", "pipe clamp install"],
    )

    prepared = {
        "ctx": {
            "full_query": "pipe DN25",
            "search_query": "pipe DN25",
            "item": {"name": "pipe", "specialty": "C10"},
        },
        "classification": {"primary": "C10", "fallbacks": []},
        "exp_backup": None,
        "rule_backup": None,
    }

    validator = MagicMock()
    validator.validate_candidates.return_value = []

    with patch("src.match_core.cascade_search", return_value=[]) as mock_cascade:
        _prepare_candidates_from_prepared(prepared, MagicMock(), None, validator)
        actual_query = mock_cascade.call_args[0][1]

    assert "pipe install galvanized" in actual_query
    assert "pipe clamp install" in actual_query


def test_active_learning_groups_emit_feedback_event(tmp_path):
    from src.active_learner import mark_learning_groups

    reset_feedback_bus(tmp_path / "feedback_bus.db")
    results = [
        {
            "bill_item": {"name": "pipe", "specialty": "C10", "params": {"dn": 25}},
            "quotas": [{"quota_id": "C10-1-1", "name": "pipe"}],
            "confidence": 60,
        },
        {
            "bill_item": {"name": "pipe", "specialty": "C10", "params": {"dn": 25}},
            "quotas": [{"quota_id": "C10-1-2", "name": "pipe"}],
            "confidence": 55,
        },
    ]

    mark_learning_groups(results)

    events = get_feedback_bus().store.list_events(event_type="active_learning_groups")
    assert events
    assert events[0]["payload"]["groups_marked"] == 1


def test_feedback_bias_falls_back_to_experience_db_when_bus_history_is_sparse(monkeypatch):
    from src.hybrid_searcher import HybridSearcher

    monkeypatch.setattr(config, "HYBRID_FEEDBACK_ADAPTIVE_BIAS", True, raising=False)
    monkeypatch.setattr(config, "HYBRID_FEEDBACK_MIN_SAMPLES", 20, raising=False)
    monkeypatch.setattr(config, "HYBRID_FEEDBACK_BIAS_MAX", 0.08, raising=False)
    monkeypatch.setattr(config, "HYBRID_FEEDBACK_BIAS_REFRESH_SEC", 0, raising=False)
    monkeypatch.setattr(
        "src.hybrid_searcher.get_feedback_bias_rows",
        lambda province, limit=2000: [("correct", "pipe DN25 galvanized steel installation")],
    )

    exp_rows = []
    for _ in range(12):
        exp_rows.append(("user_correction", "pipe DN25 galvanized steel installation"))
        exp_rows.append(("user_confirmed", "distribution box installation with foundation and commissioning"))

    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher.province = "test-province"
    searcher._feedback_bias_ts = 0.0
    searcher._feedback_bias_value = 0.0
    searcher._experience_db = MagicMock()
    searcher._experience_db.get_feedback_bias_data.return_value = exp_rows

    bias = HybridSearcher._get_feedback_bias(searcher)
    assert bias > 0
