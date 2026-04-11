import pytest
from types import SimpleNamespace

from src.match_pipeline import _prepare_item_for_matching


def test_fast_strategy_downgrades_to_standard_after_experience_miss(monkeypatch):
    item = {
        "name": "test item",
        "description": "spec:DN100",
        "unit": "m",
        "quantity": 12,
        "params": {"dn": "100"},
    }

    monkeypatch.setattr(
        "src.match_pipeline._ADAPTIVE_STRATEGY",
        SimpleNamespace(
            evaluate=lambda _: {
                "strategy": "fast",
                "complexity": 0.1,
                "hit_rate": 0.9,
                "param_completeness": 0.8,
            }
        ),
    )
    monkeypatch.setattr("src.match_pipeline.try_experience_match", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "src.match_pipeline.try_experience_exact_match",
        lambda *args, **kwargs: pytest.fail("fast strategy should not call exact-only experience path"),
    )

    prepared = _prepare_item_for_matching(
        item,
        experience_db=object(),
        rule_validator=None,
        lightweight_experience=True,
    )

    assert prepared.get("early_result") is None
    assert item["adaptive_strategy"] == "standard"
    assert item["adaptive_strategy_meta"]["downgraded_from"] == "fast"
    assert item["adaptive_strategy_meta"]["downgrade_reason"] == "experience_miss"
