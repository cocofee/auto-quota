import json

import config
from src.goal_search.oss_alias_prior import (
    GuardedOssAliasPriorSource,
    collect_guarded_oss_alias_candidates,
    normalize_alias_text,
    reset_guarded_oss_alias_prior_source,
)


def _write_index(path):
    rows = [
        {
            "normalized_query": normalize_alias_text("Alias Pipe"),
            "province": "Local Test",
            "query_family": "pipe",
            "quota_id": "P-1",
            "support_count": 3,
            "source_families": ["A", "B"],
            "source_file_hashes": ["h1", "h2", "h3"],
            "oof_folds": [1, 2, 3],
            "evidence": [
                {"source_family": "A", "source_file_hash": "h1", "oof_fold": 1},
                {"source_family": "A", "source_file_hash": "h2", "oof_fold": 2},
                {"source_family": "B", "source_file_hash": "h3", "oof_fold": 3},
            ],
        },
        {
            "normalized_query": normalize_alias_text("Alias Pipe"),
            "province": "Local Test",
            "query_family": "pipe",
            "quota_id": "P-LOW",
            "support_count": 1,
            "source_families": ["A"],
            "source_file_hashes": ["h4"],
            "oof_folds": [4],
            "evidence": [{"source_family": "A", "source_file_hash": "h4", "oof_fold": 4}],
        },
        {
            "normalized_query": normalize_alias_text("Alias Empty"),
            "province": "Local Test",
            "query_family": "",
            "quota_id": "E-1",
            "support_count": 3,
            "source_families": ["A", "B"],
            "source_file_hashes": ["h5", "h6"],
            "oof_folds": [1, 2],
            "evidence": [],
        },
    ]
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_guarded_alias_source_filters_scope_support_and_exclusions(tmp_path):
    index = tmp_path / "alias.jsonl"
    _write_index(index)

    source = GuardedOssAliasPriorSource(index, min_support=2, core_families={"pipe"})
    rows = source.collect(
        province="Local Test",
        query_text="Alias Pipe",
        query_family="pipe",
        item={"source_file_hash": "h1", "oof_fold": 1},
        top_k=5,
    )

    assert [row["quota_id"] for row in rows] == ["P-1"]
    assert rows[0]["oss_alias_support_count"] == 2
    assert rows[0]["oss_alias_source_family_count"] == 2

    blocked = source.collect(
        province="Local Test",
        query_text="Alias Empty",
        query_family="",
        item={},
        top_k=5,
    )
    assert blocked == []


def test_guarded_alias_collect_function_is_default_off(tmp_path, monkeypatch):
    index = tmp_path / "alias.jsonl"
    _write_index(index)
    monkeypatch.setattr(config, "OSS_GUARDED_ALIAS_INDEX_PATH", str(index))
    monkeypatch.setattr(config, "OSS_GUARDED_ALIAS_ENABLED", False)
    reset_guarded_oss_alias_prior_source()

    assert collect_guarded_oss_alias_candidates(
        province="Local Test",
        query_text="Alias Pipe",
        query_family="pipe",
    ) == []

    monkeypatch.setattr(config, "OSS_GUARDED_ALIAS_ENABLED", True)
    reset_guarded_oss_alias_prior_source()
    rows = collect_guarded_oss_alias_candidates(
        province="Local Test",
        query_text="Alias Pipe",
        query_family="pipe",
    )
    assert [row["quota_id"] for row in rows] == ["P-1"]
