import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from src.match_engine import (
    _get_agent_rules_context_cached,
    _get_reference_cases_cached,
)


def test_reference_cases_cache_single_flight_with_lock():
    cache = {}
    cache_lock = threading.Lock()
    call_count = 0
    call_count_lock = threading.Lock()
    expected = [{"bill": "demo", "quotas": ["Q1"]}]

    def fake_get_reference_cases(*args, **kwargs):
        nonlocal call_count
        time.sleep(0.01)
        with call_count_lock:
            call_count += 1
        return expected

    with patch("src.match_engine._get_reference_cases", side_effect=fake_get_reference_cases):
        def _worker():
            return _get_reference_cases_cached(
                cache=cache,
                cache_lock=cache_lock,
                experience_db=object(),
                full_query="消防泵安装",
                province="北京市建设工程施工消耗量标准(2024)",
                top_k=3,
                specialty="C10",
                tolerate_error=True,
            )

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda _: _worker(), range(40)))

    assert all(r == expected for r in results)
    assert call_count == 1


def test_rules_context_cache_single_flight_with_lock():
    cache = {}
    cache_lock = threading.Lock()
    call_count = 0
    call_count_lock = threading.Lock()
    expected = [{"chapter": "C10-2", "content": "排水管规则"}]

    def fake_get_rules_context(*args, **kwargs):
        nonlocal call_count
        time.sleep(0.01)
        with call_count_lock:
            call_count += 1
        return expected

    with patch("src.match_engine._get_agent_rules_context", side_effect=fake_get_rules_context):
        def _worker():
            return _get_agent_rules_context_cached(
                cache=cache,
                cache_lock=cache_lock,
                rule_kb=object(),
                name="排水塑料管安装",
                desc="DN100",
                province="北京市建设工程施工消耗量标准(2024)",
                top_k=3,
            )

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda _: _worker(), range(40)))

    assert all(r == expected for r in results)
    assert call_count == 1
