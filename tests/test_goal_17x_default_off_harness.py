import json

import config
from src.goal_search.oss_recall_prior import collect_oss_recall_candidates, reset_oss_recall_prior_source
from tools.goal_17x_default_off_harness import HARNESS_CONTRACT


def _write_17x_index(path):
    rows = []
    for idx in range(1, 5):
        rows.append(
            {
                "province": "Local Test",
                "query_family": "pipe",
                "quota_id": f"P-{idx}",
                "bill_name_key": f"pipe{idx}",
                "terms": ["pipe", "install", "steel", f"guard{idx}"],
                "bill_terms": ["pipe", "install", "steel", f"guard{idx}"],
                "quota_terms": ["steel", f"guard{idx}"],
                "quota_names": [f"steel pipe guard {idx}"],
                "support_count": 6 - idx,
                "source_families": ["oss_a"],
                "signal": {"material": "steel"},
                "evidence": [],
            }
        )
    rows.append(
        {
            "province": "Other Province",
            "query_family": "pipe",
            "quota_id": "FOREIGN-1",
            "bill_name_key": "pipeforeign",
            "terms": ["pipe", "install", "steel", "foreign"],
            "bill_terms": ["pipe", "install", "steel", "foreign"],
            "quota_terms": ["steel", "foreign"],
            "quota_names": ["foreign steel pipe"],
            "support_count": 99,
            "source_families": ["oss_a", "oss_b"],
            "signal": {"material": "steel"},
            "evidence": [],
        }
    )
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _apply_17x_contract(monkeypatch, index):
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_PATH", str(index))
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_TOP_K", HARNESS_CONTRACT["top_k"])
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_MIN_SUPPORT", HARNESS_CONTRACT["min_support"])
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_MIN_SOURCE_FAMILIES", HARNESS_CONTRACT["min_source_families"])
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_MIN_OVERLAP", HARNESS_CONTRACT["min_overlap"])
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_INTERVENTION_MODE", HARNESS_CONTRACT["intervention_mode"])
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_CORE_FAMILIES", HARNESS_CONTRACT["core_families"])
    reset_oss_recall_prior_source()


def test_17x_harness_contract_is_default_off():
    assert HARNESS_CONTRACT["enabled_by_default"] is False
    assert HARNESS_CONTRACT["top_k"] == 3
    assert HARNESS_CONTRACT["intervention_mode"] == "broad"
    assert HARNESS_CONTRACT["core_families"] == ("concrete", "pipe", "pump", "rebar", "support")


def test_17x_harness_default_off_collects_no_candidates(tmp_path, monkeypatch):
    index = tmp_path / "oss_17x.jsonl"
    _write_17x_index(index)
    _apply_17x_contract(monkeypatch, index)
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_ENABLED", False)

    rows = collect_oss_recall_candidates(
        province="Local Test",
        query_text="steel pipe install guard1 guard2 guard3",
        query_family="pipe",
    )

    assert rows == []


def test_17x_harness_explicit_enable_is_top3_and_local_province_only(tmp_path, monkeypatch):
    index = tmp_path / "oss_17x.jsonl"
    _write_17x_index(index)
    _apply_17x_contract(monkeypatch, index)
    monkeypatch.setattr(config, "OSS_RECALL_INDEX_ENABLED", True)

    rows = collect_oss_recall_candidates(
        province="Local Test",
        query_text="steel pipe install guard1 guard2 guard3 guard4 foreign",
        query_family="pipe",
    )

    quota_ids = [row["quota_id"] for row in rows]
    assert len(quota_ids) == 3
    assert "FOREIGN-1" not in quota_ids
    assert all(row["oss_recall_intervention_mode"] == "broad" for row in rows)
    assert all(row["oss_recall_source_family_count"] >= 1 for row in rows)
