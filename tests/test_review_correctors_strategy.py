from unittest.mock import patch

from src.review_correctors import _find_category_by_strategy
from src.review_correctors import _correct_category, _run_search_chain, correct_error


def test_find_category_by_strategy_continues_when_stop_false():
    strategies = {
        # 先命中这个 contains 策略，但搜索失败且 stop=False
        "地漏": {
            "match": "contains",
            "search": [["first_miss"]],
            "stop": False,
        },
        # 后续更精确策略应继续被尝试并命中
        "侧排地漏": {
            "match": "contains",
            "search": [["second_hit"]],
            "stop": True,
        },
    }

    def fake_search(keywords, **kwargs):
        if keywords == ["second_hit"]:
            return [("Q-2", "侧排地漏安装", "个")]
        return []

    with patch("src.review_correctors.CORRECTION_STRATEGIES", strategies):
        with patch("src.review_correctors.search_quota_db", side_effect=fake_search):
            result, should_stop = _find_category_by_strategy(
                core_noun="侧排地漏",
                dn=None,
                desc_lines=[],
                province="测试省份",
                conn=None,
            )

    assert result == ("Q-2", "侧排地漏安装")
    assert should_stop is True


def test_find_category_by_strategy_stops_when_stop_true():
    strategies = {
        "地漏": {
            "match": "contains",
            "search": [["first_miss"]],
            "stop": True,
        },
        "侧排地漏": {
            "match": "contains",
            "search": [["second_hit"]],
            "stop": True,
        },
    }

    with patch("src.review_correctors.CORRECTION_STRATEGIES", strategies):
        with patch("src.review_correctors.search_quota_db", return_value=[]):
            result, should_stop = _find_category_by_strategy(
                core_noun="侧排地漏",
                dn=None,
                desc_lines=[],
                province="测试省份",
                conn=None,
            )

    assert result is None
    assert should_stop is True


def test_run_search_chain_handles_invalid_search_list():
    assert _run_search_chain(None, dn=None, section=None, province=None, conn=None) is None


def test_correct_category_handles_empty_expected_without_index_error():
    item = {"name": "测试", "description": ""}
    error = {"core_noun": "地漏", "expected": []}
    with patch("src.review_correctors._find_category_by_strategy", return_value=(None, False)):
        with patch("src.review_correctors.search_quota_db", return_value=[]):
            result = _correct_category(item, error, dn=None, province="测试省份", conn=None)
    assert result is None


def test_correct_error_returns_none_when_error_type_missing():
    item = {"name": "测试", "description": ""}
    assert correct_error(item, {}, dn=None, province="测试省份", conn=None) is None


def test_correct_error_keeps_sibling_province_metadata():
    item = {"name": "测试清单", "description": ""}
    error = {"type": "category_mismatch"}

    def fake_corrector(_item, _error, _dn, province, _conn):
        if province == "兄弟库":
            return ("Q-9", "兄弟库定额")
        return None

    with patch("src.review_correctors._CORRECTOR_DISPATCH", {"category_mismatch": fake_corrector}):
        with patch("src.review_correctors.validate_correction", return_value=True):
            with patch("src.review_correctors._is_relevant_correction", return_value=True):
                result = correct_error(
                    item,
                    error,
                    dn=None,
                    province="主库",
                    conn=None,
                    sibling_provinces=["兄弟库"],
                )

    assert result == {
        "quota_id": "Q-9",
        "quota_name": "兄弟库定额",
        "province": "兄弟库",
    }
