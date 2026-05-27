import json

import config
from src.goal_search.national_index import QuotaSignal
from src.goal_search.oss_recall_prior import OssRecallPriorSource, collect_oss_recall_candidates, reset_oss_recall_prior_source


def _write_index(path):
    rows = [
        {
            "province": "Local Test",
            "query_family": "pipe",
            "quota_id": "P-1",
            "bill_name_key": "pipeinstall",
            "terms": ["pipe", "install", "dn:100", "steel"],
            "bill_terms": ["pipe", "install", "dn:100", "steel"],
            "quota_terms": ["steel", "dn:100"],
            "quota_names": ["steel pipe DN100"],
            "support_count": 3,
            "source_families": ["A", "B"],
            "signal": {"dn": 100},
            "evidence": [],
        },
        {
            "province": "Local Test",
            "query_family": "pipe",
            "quota_id": "P-DN50",
            "bill_name_key": "pipeinstall",
            "terms": ["pipe", "install", "dn:50", "steel"],
            "bill_terms": ["pipe", "install", "dn:50", "steel"],
            "quota_terms": ["steel", "dn:50"],
            "quota_names": ["steel pipe DN50"],
            "support_count": 3,
            "source_families": ["A", "B"],
            "signal": {"dn": 50},
            "evidence": [],
        },
        {
            "province": "Local Test",
            "query_family": "",
            "quota_id": "EMPTY",
            "bill_name_key": "empty",
            "terms": ["pipe", "install"],
            "bill_terms": ["pipe", "install"],
            "quota_terms": ["pipe"],
            "quota_names": ["generic pipe"],
            "support_count": 3,
            "source_families": ["A", "B"],
            "signal": {},
            "evidence": [],
        },
        {
            "province": "Local Test",
            "query_family": "support",
            "quota_id": "S-1",
            "bill_name_key": "supportinstall",
            "bill_name_keys": ["supportinstall"],
            "terms": ["support", "install", "steel", "bracket", "height"],
            "bill_terms": ["support", "install", "steel", "bracket", "height"],
            "quota_terms": ["steel", "bracket"],
            "quota_names": ["steel support bracket"],
            "support_count": 6,
            "source_families": ["A", "B"],
            "signal": {},
            "evidence": [],
        },
    ]
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_oss_recall_source_filters_family_support_and_numeric_conflict(tmp_path):
    index = tmp_path / "recall.jsonl"
    _write_index(index)
    source = OssRecallPriorSource(index, min_support=2, min_overlap=2, intervention_mode="broad", core_families={"pipe"})

    rows = source.collect(
        province="Local Test",
        query_text="pipe install DN100 steel",
        query_family="pipe",
        top_k=5,
    )

    assert [row["quota_id"] for row in rows] == ["P-1"]
    assert rows[0]["oss_recall_overlap"] >= 2
    assert rows[0]["oss_recall_source_family_count"] == 2

    assert source.collect(province="Local Test", query_text="pipe install", query_family="", top_k=5) == []


def test_oss_recall_collect_function_is_default_off(tmp_path, monkeypatch):
    index = tmp_path / "recall.jsonl"
    _write_index(index)
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_PATH", str(index))
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_ENABLED", False)
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_MIN_SUPPORT", 2)
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_MIN_SOURCE_FAMILIES", 2)
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_MIN_OVERLAP", 2)
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_INTERVENTION_MODE", "broad")
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_CORE_FAMILIES", ("pipe",))
    reset_oss_recall_prior_source()

    assert collect_oss_recall_candidates(province="Local Test", query_text="pipe install DN100 steel", query_family="pipe") == []

    monkeypatch.setattr(config, "OSS_RECALL_INDEX_ENABLED", True)
    reset_oss_recall_prior_source()
    rows = collect_oss_recall_candidates(province="Local Test", query_text="pipe install DN100 steel", query_family="pipe")
    assert [row["quota_id"] for row in rows] == ["P-1"]


def test_oss_recall_safe_enabled_defaults_are_support_exact_name(tmp_path, monkeypatch):
    index = tmp_path / "recall.jsonl"
    _write_index(index)
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_PATH", str(index))
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_ENABLED", True)
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_TOP_K", 1)
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_MIN_SUPPORT", 6)
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_MIN_SOURCE_FAMILIES", 2)
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_MIN_OVERLAP", 4)
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_INTERVENTION_MODE", "exact_name")
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_CORE_FAMILIES", ("support",))
    reset_oss_recall_prior_source()

    assert collect_oss_recall_candidates(
        province="Local Test",
        query_text="pipe install DN100 steel",
        query_family="pipe",
        item={"bill_name": "pipeinstall"},
    ) == []

    rows = collect_oss_recall_candidates(
        province="Local Test",
        query_text="support install steel bracket height",
        query_family="support",
        item={"bill_name": "supportinstall"},
    )

    assert [row["quota_id"] for row in rows] == ["S-1"]
    assert rows[0]["oss_recall_exact_name"] is True
    assert rows[0]["oss_recall_intervention_mode"] == "exact_name"


def test_oss_recall_blocks_generic_family_only_matches(tmp_path):
    index = tmp_path / "recall.jsonl"
    rows = [
        {
            "province": "Local Test",
            "query_family": "pipe",
            "quota_id": "P-GENERIC",
            "bill_name_key": "generic",
            "terms": ["pipe", "install"],
            "bill_terms": ["pipe", "install"],
            "quota_terms": ["pipe"],
            "quota_names": ["pipe install"],
            "support_count": 4,
            "source_families": ["A", "B"],
            "signal": {},
            "evidence": [],
        }
    ]
    with index.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    source = OssRecallPriorSource(index, min_support=2, min_overlap=2, min_specific_overlap=1, core_families={"pipe"})
    assert source.collect(province="Local Test", query_text="pipe install", query_family="pipe", top_k=5) == []


def test_oss_recall_exact_name_mode_blocks_non_exact_candidates(tmp_path):
    index = tmp_path / "recall.jsonl"
    _write_index(index)
    source = OssRecallPriorSource(
        index,
        min_support=2,
        min_overlap=2,
        intervention_mode="exact_name",
        core_families={"pipe"},
    )

    assert source.collect(
        province="Local Test",
        query_text="steel pipe DN100 install",
        query_family="pipe",
        item={"bill_name": "different bill name"},
        top_k=5,
    ) == []

    rows = source.collect(
        province="Local Test",
        query_text="pipe install DN100 steel",
        query_family="pipe",
        item={"bill_name": "pipeinstall"},
        top_k=5,
    )

    assert [row["quota_id"] for row in rows] == ["P-1"]
    assert rows[0]["oss_recall_exact_name"] is True
    assert rows[0]["oss_recall_intervention_mode"] == "exact_name"


def test_oss_recall_detects_observable_signal_conflicts():
    candidate = type(
        "Candidate",
        (),
        {"signal": {"action": "install", "material": "steel", "connection": "flange", "install_method": "surface"}},
    )()

    assert OssRecallPriorSource._conflicts(QuotaSignal(action="install", material="copper"), candidate)
    assert OssRecallPriorSource._conflicts(QuotaSignal(connection="threaded"), candidate)
    assert not OssRecallPriorSource._conflicts(QuotaSignal(action="install", material="steel"), candidate)
