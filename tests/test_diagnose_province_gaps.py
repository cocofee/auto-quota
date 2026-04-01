from tools.diagnose_province_gaps import build_province_gap_report, recommend_focus


def test_recommend_focus_prioritizes_synonym_and_query_dilution():
    actions = recommend_focus(
        baseline_diag={"synonym_gap": 12, "wrong_tier": 3, "wrong_book": 1},
        recall_categories={"synonym_gap": 30, "query_dilution": 15, "rule_mislead": 2},
        rate=12.0,
    )

    assert "priority_synonym_recall" in actions
    assert "priority_query_dilution" in actions
    assert "province_red_alert" in actions


def test_build_province_gap_report_sorts_by_priority():
    baseline = {
        "provinces": {
            "A省": {
                "total": 50,
                "rate": 10.0,
                "diagnosis": {"synonym_gap": 20, "wrong_tier": 5},
            },
            "B省": {
                "total": 50,
                "rate": 35.0,
                "diagnosis": {"wrong_tier": 12},
            },
        }
    }
    recall_report = {
        "recall_gaps": {
            "province_recall_miss_counts": {
                "A省": 80,
                "B省": 10,
            },
            "province_category_counts": {
                "A省": {"synonym_gap": 40, "query_dilution": 10},
                "B省": {"query_dilution": 2},
            },
        }
    }

    report = build_province_gap_report(baseline, recall_report, top_n=2)

    assert report["priority_provinces"][0]["province"] == "A省"
    assert report["priority_provinces"][0]["recommended_actions"][0] == "priority_synonym_recall"
    assert report["priority_provinces"][1]["province"] == "B省"
