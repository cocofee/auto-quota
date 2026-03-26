from src.unified_planner import build_unified_search_plan


def test_unified_planner_builds_soft_plan_from_titles():
    plan = build_unified_search_plan(
        province="安徽省安装工程计价定额(2018)",
        item={
            "name": "复合管",
            "description": "介质:给水 材质、规格:钢塑复合压力给水管 DN25",
            "project_name": "4-2单元-给排水工程",
            "bill_name": "给排水工程(单位工程)",
        },
        context_prior={
            "project_name": "4-2单元-给排水工程",
            "bill_name": "给排水工程(单位工程)",
        },
        canonical_features={"family": "pipe_support", "entity": "pipe"},
    )

    assert plan["primary_book"] == "C10"
    assert plan["route_mode"] == "moderate"
    assert plan["allow_cross_book_escape"] is True
    assert plan["hard_books"] == []
    assert "C10" in plan["preferred_books"]


def test_unified_planner_uses_plugin_aliases_and_family_cluster_as_soft_plan():
    plan = build_unified_search_plan(
        province="重庆市通用安装工程计价定额(2018)",
        item={
            "name": "自动排气阀",
            "description": "型号、规格：DN25",
        },
        canonical_features={"family": "valve_body", "entity": "valve"},
        plugin_hints={
            "preferred_books": ["C10"],
            "preferred_specialties": ["C10"],
            "synonym_aliases": ["排气阀安装"],
        },
    )

    assert plan["primary_book"] == "C10"
    assert plan["route_mode"] == "moderate"
    assert plan["allow_cross_book_escape"] is True
    assert plan["search_aliases"] == ["排气阀安装"]
    assert "C10" in plan["preferred_books"]
