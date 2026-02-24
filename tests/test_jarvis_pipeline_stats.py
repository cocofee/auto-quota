from tools.jarvis_pipeline import _build_pipeline_stats, _count_manual_review_rows


def test_count_manual_review_rows_excludes_cross_item_reminders():
    manual_items = [
        {"seq": 3, "name": "普通人工项"},
        {"seq": 0, "name": "【跨项提醒】", "reason": "缺少配套项"},
        {"seq": "4", "name": "另一条人工项"},
    ]
    rows, reminders = _count_manual_review_rows(manual_items)
    assert rows == 2
    assert reminders == 1


def test_build_pipeline_stats_not_affected_by_reminders():
    results = [{"match_source": "agent"} for _ in range(2)]
    auto_corrections = []
    manual_items = [{"seq": 0, "name": "【跨项提醒】"}]
    measure_items = []

    stats = _build_pipeline_stats(results, auto_corrections, manual_items, measure_items)

    assert stats["total"] == 2
    assert stats["manual"] == 0
    assert stats["manual_reminders"] == 1
    assert stats["correct"] == 2


def test_build_pipeline_stats_counts_agent_error_as_fallback():
    results = [
        {"match_source": "agent"},
        {"match_source": "agent_fallback"},
        {"match_source": "agent_error"},
    ]

    stats = _build_pipeline_stats(results, [], [], [])

    assert stats["fallback"] == 2
