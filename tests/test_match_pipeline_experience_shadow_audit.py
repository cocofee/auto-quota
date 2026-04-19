from src.match_pipeline import (
    _append_experience_shadow_audit_trace,
    _prepare_item_for_matching,
)


def test_prepare_item_samples_experience_direct_into_shadow_audit(monkeypatch):
    item = {
        "name": "test item",
        "description": "spec:DN100",
        "unit": "m",
        "quantity": 12,
        "params": {"dn": "100"},
    }
    exp_result = {
        "quotas": [{"quota_id": "Q-EXP", "name": "experience quota"}],
        "confidence": 93,
        "match_source": "experience_exact",
        "trace": {"steps": [], "path": []},
    }

    monkeypatch.setattr("src.match_pipeline.try_experience_exact_match", lambda *args, **kwargs: dict(exp_result))
    monkeypatch.setattr("src.match_pipeline._review_check_match_result", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.match_pipeline._prepare_rule_match", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr("src.match_pipeline.orchestrator._EXPERIENCE_SHADOW_AUDIT_COUNTER", iter([2]))
    monkeypatch.setattr("config.EXPERIENCE_SHADOW_AUDIT_EVERY_N", 2, raising=False)

    prepared = _prepare_item_for_matching(
        item,
        experience_db=object(),
        rule_validator=None,
        exact_exp_direct=True,
        lightweight_experience=True,
    )

    assert prepared["early_result"] is None
    assert prepared["exp_backup"]["match_source"] == "experience_exact"
    assert item["_experience_shadow_audit"]["sampled"] is True
    assert prepared["exp_backup"]["trace"]["steps"][-1]["stage"] == "experience_shadow_audit_sampled"


def test_shadow_audit_trace_alerts_on_divergence():
    result = {
        "quotas": [{"quota_id": "Q-SEARCH"}],
        "confidence": 78,
        "trace": {"steps": [], "path": []},
    }
    exp_backup = {
        "quotas": [{"quota_id": "Q-EXP"}],
        "confidence": 93,
        "match_source": "experience_exact",
    }
    item = {
        "_experience_shadow_audit": {
            "sampled": True,
            "sequence": 10,
            "sample_every": 5,
        }
    }

    _append_experience_shadow_audit_trace(result, exp_backup, item)

    step = result["trace"]["steps"][-1]
    assert step["stage"] == "experience_shadow_audit"
    assert step["diverged"] is True
    assert step["alert"] is True
    assert step["experience_quota_id"] == "Q-EXP"
    assert step["final_quota_id"] == "Q-SEARCH"


def test_rule_direct_early_return_keeps_shadow_audit_trace(monkeypatch):
    item = {
        "name": "test item",
        "description": "spec:DN100",
        "unit": "m",
        "quantity": 12,
        "params": {"dn": "100"},
    }
    exp_result = {
        "quotas": [{"quota_id": "Q-EXP", "name": "experience quota"}],
        "confidence": 93,
        "match_source": "experience_exact",
        "trace": {"steps": [], "path": []},
    }
    rule_direct = {
        "quotas": [{"quota_id": "Q-RULE", "name": "rule quota"}],
        "confidence": 88,
        "match_source": "rule_direct",
        "trace": {"steps": [], "path": []},
    }

    monkeypatch.setattr("src.match_pipeline.try_experience_exact_match", lambda *args, **kwargs: dict(exp_result))
    monkeypatch.setattr("src.match_pipeline._review_check_match_result", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.match_pipeline.orchestrator._prepare_rule_match", lambda *args, **kwargs: (dict(rule_direct), None))
    monkeypatch.setattr("src.match_pipeline.orchestrator._EXPERIENCE_SHADOW_AUDIT_COUNTER", iter([2]))
    monkeypatch.setattr("config.EXPERIENCE_SHADOW_AUDIT_EVERY_N", 2, raising=False)

    prepared = _prepare_item_for_matching(
        item,
        experience_db=object(),
        rule_validator=None,
        exact_exp_direct=True,
        lightweight_experience=True,
    )

    assert prepared["early_type"] == "rule_direct"
    steps = prepared["early_result"]["trace"]["steps"]
    assert any(step["stage"] == "experience_shadow_audit" for step in steps)
