from tools import v36_speed_probe


def test_detail_rows_default_keeps_slowest_10_contract():
    payload = {
        "slowest_10": [{"sample_id": "slow"}],
        "item_rows": [{"sample_id": "all"}],
    }

    assert v36_speed_probe._detail_rows(payload, "slowest_10") == [{"sample_id": "slow"}]


def test_detail_rows_all_exports_full_item_rows():
    payload = {
        "slowest_10": [{"sample_id": "slow"}],
        "item_rows": [{"sample_id": "a"}, {"sample_id": "b"}],
    }

    assert v36_speed_probe._detail_rows(payload, "all") == [
        {"sample_id": "a"},
        {"sample_id": "b"},
    ]


def test_summary_payload_excludes_heavy_detail_rows():
    payload = {
        "evaluated_total": 100,
        "slowest_10": [{"sample_id": "slow"}],
        "item_rows": [{"sample_id": "a"}],
    }

    assert v36_speed_probe._summary_payload(payload) == {"evaluated_total": 100}
