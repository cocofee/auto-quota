from src.context_builder import (
    apply_batch_context,
    build_context_prior,
    build_project_context,
    format_overview_context,
)


def test_build_context_prior_keeps_existing_fields_and_dedupes_hints():
    context = build_context_prior({
        "specialty": "C4",
        "specialty_name": "电气",
        "_context_hints": ["桥架", "桥架", "电缆"],
        "_prior_family": "支架",
        "cable_type": "光缆",
        "section": "电气工程",
    })

    assert context["specialty"] == "C4"
    assert context["specialty_name"] == "电气"
    assert context["context_hints"][:2] == ["桥架", "电缆"]
    assert context["prior_family"] == "支架"
    assert context["cable_type"] == "光缆"
    assert context["system_hint"] == "电气"
    assert context["batch_context"]["batch_size"] == 0


def test_build_project_context_detects_primary_specialty_and_system():
    context = build_project_context([
        {"specialty": "C4", "section": "电气工程", "name": "配线"},
        {"specialty": "C4", "section": "电气工程", "name": "桥架"},
        {"specialty": "C10", "section": "给排水工程", "name": "给水管"},
    ])

    assert context["primary_specialty"] == "C4"
    assert context["system_hint"] == "电气"
    assert context["context_hints"] == ["电气"]
    assert context["section_system_hints"]["电气工程"] == "电气"


def test_apply_batch_context_adds_neighbor_and_section_system_hints():
    items = [
        {"name": "桥架", "description": "", "section": "电气工程", "sheet_name": "安装", "specialty": "C4"},
        {"name": "支架", "description": "", "section": "电气工程", "sheet_name": "安装", "specialty": "C4"},
        {"name": "电缆", "description": "WDZN-YJY 4x25", "section": "电气工程", "sheet_name": "安装", "specialty": "C4"},
    ]

    project_context = build_project_context(items)
    apply_batch_context(
        items,
        project_context=project_context,
        is_ambiguous_fn=lambda item: item.get("name") == "支架",
        short_name_priors={},
    )

    ambiguous = items[1]
    assert ambiguous["_is_ambiguous_short"] is True
    assert ambiguous["_context_hints"]
    assert ambiguous["_batch_context"]["section_system_hint"] == "电气"
    assert ambiguous["_batch_context"]["project_system_hint"] == "电气"
    assert ambiguous["_batch_context"]["neighbor_system_hint"] == "电气"

    context_prior = build_context_prior(ambiguous, project_context=project_context)
    assert context_prior["system_hint"] == "电气"
    assert "电气" in context_prior["context_hints"]
    assert context_prior["batch_context"]["section_system_hint"] == "电气"
    assert context_prior["batch_context"]["batch_size"] == 3


def test_apply_batch_context_falls_back_to_short_name_priors():
    items = [
        {"name": "水箱", "description": "", "section": "", "sheet_name": "", "specialty": "C9"},
    ]

    project_context = build_project_context(items)
    apply_batch_context(
        items,
        project_context=project_context,
        is_ambiguous_fn=lambda item: True,
        short_name_priors={("水箱", "C9"): "消防水箱"},
    )

    assert items[0]["_prior_family"] == "消防水箱"


def test_format_overview_context_includes_batch_theme_and_item_context():
    item = {
        "section": "电气工程",
        "sheet_name": "安装",
        "context_prior": {
            "context_hints": ["桥架", "电缆"],
            "batch_context": {
                "project_system_hint": "电气",
                "section_system_hint": "电气",
                "sheet_system_hint": "电气",
                "neighbor_system_hint": "电气",
                "batch_size": 12,
            },
        },
    }
    text = format_overview_context(
        item=item,
        project_context={
            "batch_size": 12,
            "primary_specialty": "C4",
            "system_hint": "电气",
        },
        project_overview="本项目为机电安装工程。",
        match_stats=["桥架 → C4-1-1(钢制桥架) : 3条", "配线 → C4-2-1(管内穿线) : 2条"],
    )

    assert "本项目为机电安装工程。" in text
    assert "批次规模: 12条" in text
    assert "主专业: C4" in text
    assert "当前条目批次上下文" in text
    assert "上下文提示: 桥架, 电缆" in text
    assert "已处理的同类清单匹配情况" in text
