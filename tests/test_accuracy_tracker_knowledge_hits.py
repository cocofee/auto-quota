import src.accuracy_tracker as accuracy_tracker_mod
from src.accuracy_tracker import AccuracyTracker


def test_record_knowledge_hits_and_report(tmp_path, monkeypatch):
    db_path = tmp_path / "run_history.db"
    monkeypatch.setattr(accuracy_tracker_mod, "_DB_PATH", db_path)

    tracker = AccuracyTracker()
    tracker.record_knowledge_hits(
        [
            {
                "match_source": "experience_exact",
                "confidence": 95,
                "review_risk": "low",
                "light_status": "green",
                "trace": {
                    "steps": [
                        {"stage": "experience_exact", "record_id": 1},
                    ],
                },
            },
            {
                "match_source": "agent",
                "confidence": 92,
                "review_risk": "low",
                "light_status": "green",
                "rule_hints": "系数1.2",
                "trace": {
                    "steps": [
                        {
                            "stage": "agent_llm",
                            "reference_cases_count": 2,
                            "reference_case_ids": ["201", "202"],
                            "rules_context_count": 1,
                            "rule_context_ids": ["rule_31"],
                            "method_cards_count": 1,
                            "method_card_ids": ["8"],
                        },
                    ],
                },
            },
            {
                "match_source": "rule",
                "confidence": 88,
                "review_risk": "medium",
                "light_status": "yellow",
                "trace": {
                    "steps": [
                        {"stage": "agent_llm", "rules_context_count": 2, "rule_context_ids": ["rule_41", "rule_42"]},
                    ],
                },
            },
        ],
        input_file="demo.xlsx",
        mode="agent",
        province="Beijing2024",
        task_id="task-001",
    )

    report = tracker.get_knowledge_hit_report(days=30)
    assert report["summary"]["tracked_runs"] == 1
    assert report["summary"]["tracked_results"] == 3
    assert report["summary"]["last_7d_hits"] == 5
    assert report["summary"]["last_7d_direct"] == 2

    by_layer = {item["layer"]: item for item in report["layer_metrics"]}

    exp = by_layer["ExperienceDB"]
    assert exp["hit_count"] == 2
    assert exp["direct_count"] == 1
    assert exp["assist_count"] == 1
    assert exp["high_conf_count"] == 2
    assert exp["low_risk_count"] == 2
    assert exp["hit_rate"] == 66.7

    rule = by_layer["RuleKnowledge"]
    assert rule["hit_count"] == 2
    assert rule["direct_count"] == 1
    assert rule["assist_count"] == 1
    assert rule["hint_count"] == 1
    assert rule["high_conf_count"] == 1

    method = by_layer["MethodCards"]
    assert method["hit_count"] == 1
    assert method["direct_count"] == 0
    assert method["assist_count"] == 1
    assert method["high_conf_count"] == 1
    assert method["low_risk_count"] == 1

    assert len(report["recent_activity"]) == 1
    recent = report["recent_activity"][0]
    assert recent["total_results"] == 3
    assert recent["experience_hits"] == 2
    assert recent["rule_hits"] == 2
    assert recent["method_hits"] == 1

    details = tracker.get_recent_knowledge_hit_details(days=30)
    assert len(details) == 7
    detail_layers = {(item["layer"], item["hit_type"]) for item in details}
    assert ("ExperienceDB", "direct") in detail_layers
    assert ("ExperienceDB", "assist") in detail_layers
    assert ("RuleKnowledge", "direct") in detail_layers
    assert ("RuleKnowledge", "assist") in detail_layers
    assert ("MethodCards", "assist") in detail_layers
    object_refs = {item["object_ref"] for item in details}
    assert "experience:1" in object_refs
    assert "experience:201" in object_refs
    assert "rule:rule_31" in object_refs
    assert "rule:rule_41" in object_refs
    assert "method_card:8" in object_refs
