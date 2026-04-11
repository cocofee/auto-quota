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


def test_unified_planner_suppresses_generated_support_bias_for_device_subject():
    plan = build_unified_search_plan(
        province="上海市园林工程预算定额(2016)",
        item={
            "name": "组串式逆变器",
            "description": "规格型号:150KW 安装点离地高度:屋面支架安装 布置场地:光伏场区",
        },
        context_prior={
            "primary_subject": "组串式逆变器",
            "primary_query_profile": {
                "primary_subject": "组串式逆变器",
                "primary_text": "组串式逆变器 规格型号:150KW",
            },
        },
        canonical_features={"family": "pipe_support", "entity": "支吊架", "system": "给排水"},
        plugin_hints={
            "source": "generated_benchmark_knowledge",
            "preferred_books": ["C10"],
            "preferred_specialties": ["C10"],
            "synonym_aliases": ["侧向支撑 KZS-DN100-T"],
            "matched_terms": ["支吊架"],
        },
    )

    assert plan["family"] == ""
    assert plan["search_aliases"] == []
    assert "family_cluster" not in plan["reason_tags"]
    assert "primary_subject_guard" in plan["reason_tags"]


def test_unified_planner_keeps_explicit_pipe_support_subject_family():
    plan = build_unified_search_plan(
        province="云南省通用安装工程计价标准(2020)",
        item={
            "name": "管道支架",
            "description": "材质:型钢 管架形式:一般管架",
        },
        context_prior={
            "primary_subject": "管道支架",
            "primary_query_profile": {
                "primary_subject": "管道支架",
                "primary_text": "管道支架 材质:型钢",
            },
        },
        canonical_features={"family": "pipe_support", "entity": "支吊架", "system": "给排水"},
        plugin_hints={
            "source": "generated_benchmark_knowledge",
            "preferred_books": ["C10"],
            "synonym_aliases": ["管道支架制作安装"],
        },
    )

    assert plan["family"] == "pipe_support"
    assert "family_cluster" in plan["reason_tags"]
    assert plan["search_aliases"] == ["管道支架制作安装"]


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


def test_unified_planner_filters_broad_books_out_of_install_scope():
    plan = build_unified_search_plan(
        province="上海市安装工程预算定额(2016)",
        item={
            "name": "流量开关",
            "description": "型号:DN50",
        },
        plugin_hints={
            "source": "generated_benchmark_knowledge",
            "preferred_books": ["D", "A"],
            "preferred_specialties": ["D"],
        },
    )

    assert "A" not in plan["preferred_books"]
    assert "D" not in plan["preferred_books"]
    assert "A" not in plan["plugin_hints"]["preferred_books"]
    assert "D" not in plan["plugin_hints"]["preferred_books"]
