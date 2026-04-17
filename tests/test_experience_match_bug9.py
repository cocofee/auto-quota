from unittest.mock import MagicMock, patch

import config

from src import match_core


def test_validate_experience_params_rejects_when_any_quota_mismatches(monkeypatch):
    exp_result = {
        "quotas": [
            {"quota_id": "Q-OK", "name": "管道安装 DN150"},
            {"quota_id": "Q-BAD", "name": "管道安装 DN100"},
        ]
    }
    item = {"name": "给水管道 DN150", "description": ""}

    def fake_parse(text: str):
        if "DN150" in text:
            return {"dn": 150}
        if "DN100" in text:
            return {"dn": 100}
        return {}

    def fake_params_match(bill_params: dict, quota_params: dict):
        return (bill_params["dn"] == quota_params["dn"], 1.0 if bill_params["dn"] == quota_params["dn"] else 0.0)

    monkeypatch.setattr(match_core.text_parser, "parse", fake_parse)
    monkeypatch.setattr(match_core.text_parser, "params_match", fake_params_match)

    validated = match_core._validate_experience_params(
        exp_result,
        item,
        rule_validator=None,
        is_exact=True,
    )

    assert validated is None


def test_try_experience_match_sanitizes_cross_province_hints():
    mock_exp_db = MagicMock()
    mock_exp_db.search_similar.return_value = []
    mock_exp_db.search_cross_province.return_value = [
        {"quota_names": ["  管道安装   镀锌钢管  ", "", "管卡安装", "管卡安装"]},
        {"quota_names": " 管道安装 镀锌钢管 "},
        {"quota_names": ["保温", None, "   "]},
    ]

    item = {"name": "给水管道", "description": "DN25"}

    with patch.object(config, "CROSS_PROVINCE_WARMUP_ENABLED", True):
        result = match_core.try_experience_match(
            "给水管道 DN25", item, mock_exp_db, province="广东2024")

    assert result is None
    assert item["_cross_province_hints"] == [
        "管道安装 镀锌钢管",
        "管卡安装",
        "保温",
    ]


def test_prepare_candidates_from_prepared_sanitizes_and_dedupes_hints():
    item = {
        "name": "给水管道",
        "_cross_province_hints": [
            "  管道安装   镀锌钢管  ",
            "管卡安装",
            "管卡安装",
            "",
            "给水管道 DN25",
        ],
    }
    prepared = {
        "ctx": {
            "full_query": "给水管道 DN25",
            "search_query": "给水管道 DN25 管卡安装",
            "item": item,
        },
        "classification": {"primary": "C10", "fallbacks": []},
        "exp_backup": None,
        "rule_backup": None,
    }

    mock_searcher = MagicMock()
    mock_searcher.search.return_value = []
    mock_validator = MagicMock()
    mock_validator.validate_candidates.return_value = []

    with patch("src.match_core.cascade_search", return_value=[]) as mock_cascade:
        with patch("src.match_core._build_support_surface_process_quotas", return_value=[]):
            match_core._prepare_candidates_from_prepared(
                prepared, mock_searcher, None, mock_validator)

    actual_query = mock_cascade.call_args[0][1]
    assert actual_query.count("管卡安装") == 1
    assert actual_query.count("给水管道 DN25") == 1
    assert "管道安装 镀锌钢管" in actual_query
    assert item["_cross_province_hints"] == [
        "管道安装 镀锌钢管",
        "管卡安装",
        "给水管道 DN25",
    ]
