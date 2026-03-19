from tools.run_install_smoke import (
    build_result_snapshot,
    filter_install_items,
    select_smoke_items,
)


def test_filter_install_items_keeps_install_scope():
    items = [
        {"name": "配电箱", "description": "明装", "specialty": "C4"},
        {"name": "混凝土垫层", "description": "100厚", "specialty": "A"},
        {"name": "支架", "description": "电缆桥架支撑架", "specialty": ""},
    ]

    filtered = filter_install_items(items)

    assert [item["name"] for item in filtered] == ["配电箱", "支架"]


def test_select_smoke_items_prefers_explicit_item_index():
    items = [{"name": "A"}, {"name": "B"}, {"name": "C"}]

    selected = select_smoke_items(items, limit=1, item_index=2)

    assert selected == [{"name": "B"}]


def test_build_result_snapshot_keeps_batch_context_and_trace_tail():
    result = {
        "bill_item": {
            "name": "镀锌钢管",
            "description": "DN100 丝接",
            "specialty": "C10",
            "params": {"dn": 100},
            "context_prior": {
                "context_hints": ["消防", "给排水"],
                "system_hint": "消防",
                "batch_context": {
                    "project_system_hint": "消防",
                    "section_system_hint": "消防",
                    "batch_size": 8,
                },
            },
        },
        "quotas": [
            {
                "quota_id": "Q-1",
                "name": "镀锌钢管丝接安装",
                "unit": "m",
                "param_score": 0.96,
            }
        ],
        "confidence": 88,
        "match_source": "search",
        "reasoning_decision": {"route": "installation_spec", "risk_level": "medium"},
        "needs_reasoning": True,
        "require_final_review": True,
        "final_validation": {"status": "manual_review"},
        "trace": {
            "path": ["search_select", "final_validate"],
            "steps": [
                {
                    "stage": "search_select",
                    "candidates_count": 12,
                    "batch_context": {"system_hint": "消防"},
                    "candidates": [{"quota_id": "Q-1", "name": "镀锌钢管丝接安装"}],
                },
                {
                    "stage": "final_validate",
                    "final_validation": {"status": "manual_review"},
                    "final_confidence": 88,
                },
            ],
        },
    }

    snapshot = build_result_snapshot(result, index=1, trace_tail=1)

    assert snapshot["batch_context"]["system_hint"] == "消防"
    assert snapshot["batch_context"]["project_system_hint"] == "消防"
    assert snapshot["trace_path"] == ["search_select", "final_validate"]
    assert snapshot["trace_tail"] == [
        {
            "stage": "final_validate",
            "final_validation": {"status": "manual_review"},
            "final_confidence": 88,
        }
    ]
