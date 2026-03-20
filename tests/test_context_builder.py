from src.context_builder import (
    apply_batch_context,
    build_context_prior,
    build_project_context,
    format_overview_context,
)


def test_build_context_prior_keeps_existing_fields_and_dedupes_hints():
    context = build_context_prior({
        "specialty": "C4",
        "specialty_name": "\u7535\u6c14",
        "_context_hints": ["\u6865\u67b6", "\u6865\u67b6", "\u7535\u7f06"],
        "_prior_family": "\u652f\u67b6",
        "cable_type": "\u5149\u7f06",
        "section": "\u7535\u6c14\u5de5\u7a0b",
    })

    assert context["specialty"] == "C4"
    assert context["specialty_name"] == "\u7535\u6c14"
    assert context["context_hints"][:2] == ["\u6865\u67b6", "\u7535\u7f06"]
    assert context["prior_family"] == "\u652f\u67b6"
    assert context["cable_type"] == "\u5149\u7f06"
    assert context["system_hint"] == "\u7535\u6c14"
    assert context["batch_context"]["batch_size"] == 0


def test_build_project_context_detects_primary_specialty_and_system():
    context = build_project_context([
        {"specialty": "C4", "section": "\u7535\u6c14\u5de5\u7a0b", "name": "\u914d\u7ebf"},
        {"specialty": "C4", "section": "\u7535\u6c14\u5de5\u7a0b", "name": "\u6865\u67b6"},
        {"specialty": "C10", "section": "\u7ed9\u6392\u6c34\u5de5\u7a0b", "name": "\u7ed9\u6c34\u7ba1"},
    ])

    assert context["primary_specialty"] == "C4"
    assert context["system_hint"] == "\u7535\u6c14"
    assert context["context_hints"] == ["\u7535\u6c14"]
    assert context["section_system_hints"]["\u7535\u6c14\u5de5\u7a0b"] == "\u7535\u6c14"


def test_apply_batch_context_adds_neighbor_and_section_system_hints():
    items = [
        {"name": "\u6865\u67b6", "description": "", "section": "\u7535\u6c14\u5de5\u7a0b", "sheet_name": "\u5b89\u88c5", "specialty": "C4"},
        {"name": "\u652f\u67b6", "description": "", "section": "\u7535\u6c14\u5de5\u7a0b", "sheet_name": "\u5b89\u88c5", "specialty": "C4"},
        {"name": "\u7535\u7f06", "description": "WDZN-YJY 4x25", "section": "\u7535\u6c14\u5de5\u7a0b", "sheet_name": "\u5b89\u88c5", "specialty": "C4"},
    ]

    project_context = build_project_context(items)
    apply_batch_context(
        items,
        project_context=project_context,
        is_ambiguous_fn=lambda item: item.get("name") == "\u652f\u67b6",
        short_name_priors={},
    )

    ambiguous = items[1]
    assert ambiguous["_is_ambiguous_short"] is True
    assert ambiguous["_context_hints"]
    assert ambiguous["_batch_context"]["section_system_hint"] == "\u7535\u6c14"
    assert ambiguous["_batch_context"]["project_system_hint"] == "\u7535\u6c14"
    assert ambiguous["_batch_context"]["neighbor_system_hint"] == "\u7535\u6c14"

    context_prior = build_context_prior(ambiguous, project_context=project_context)
    assert context_prior["system_hint"] == "\u7535\u6c14"
    assert "\u7535\u6c14" in context_prior["context_hints"]
    assert context_prior["batch_context"]["section_system_hint"] == "\u7535\u6c14"
    assert context_prior["batch_context"]["batch_size"] == 3


def test_apply_batch_context_falls_back_to_short_name_priors():
    items = [
        {"name": "\u6c34\u7bb1", "description": "", "section": "", "sheet_name": "", "specialty": "C9"},
    ]

    project_context = build_project_context(items)
    apply_batch_context(
        items,
        project_context=project_context,
        is_ambiguous_fn=lambda item: True,
        short_name_priors={("\u6c34\u7bb1", "C9"): "\u6d88\u9632\u6c34\u7bb1"},
    )

    assert items[0]["_prior_family"] == "\u6d88\u9632\u6c34\u7bb1"


def test_format_overview_context_includes_batch_theme_and_item_context():
    item = {
        "section": "\u7535\u6c14\u5de5\u7a0b",
        "sheet_name": "\u5b89\u88c5",
        "context_prior": {
            "context_hints": ["\u6865\u67b6", "\u7535\u7f06"],
            "batch_context": {
                "project_system_hint": "\u7535\u6c14",
                "section_system_hint": "\u7535\u6c14",
                "sheet_system_hint": "\u7535\u6c14",
                "neighbor_system_hint": "\u7535\u6c14",
                "batch_size": 12,
            },
        },
    }
    text = format_overview_context(
        item=item,
        project_context={
            "batch_size": 12,
            "primary_specialty": "C4",
            "system_hint": "\u7535\u6c14",
        },
        project_overview="\u672c\u9879\u76ee\u4e3a\u673a\u7535\u5b89\u88c5\u5de5\u7a0b\u3002",
        match_stats=[
            "\u6865\u67b6 -> C4-1-1(\u94a2\u5236\u6865\u67b6) : 3\u6761",
            "\u914d\u7ebf -> C4-2-1(\u7ba1\u5185\u7a7f\u7ebf) : 2\u6761",
        ],
    )

    assert "\u672c\u9879\u76ee\u4e3a\u673a\u7535\u5b89\u88c5\u5de5\u7a0b\u3002" in text
    assert "12" in text
    assert "C4" in text
    assert "\u6865\u67b6" in text
    assert "\u7535\u7f06" in text


def test_format_overview_context_includes_canonical_query_summary():
    item = {
        "section": "\u7535\u6c14\u5de5\u7a0b",
        "sheet_name": "\u5b89\u88c5",
        "canonical_query": {
            "route_query": "\u7535\u529b\u7535\u7f06 WDZN-YJY 3x4+2x2.5",
            "validation_query": "\u7535\u529b\u7535\u7f06 \u6865\u67b6\u6577\u8bbe WDZN-YJY 3x4+2x2.5",
            "search_query": "\u7535\u529b\u7535\u7f06 \u6865\u67b6\u6577\u8bbe WDZN-YJY 3x4+2x2.5 \u963b\u71c3",
        },
        "context_prior": {
            "context_hints": ["\u6865\u67b6"],
            "batch_context": {"batch_size": 4},
        },
    }

    text = format_overview_context(
        item=item,
        project_context={"batch_size": 4, "primary_specialty": "C4", "system_hint": "\u7535\u6c14"},
    )

    assert "RouteQuery:" in text
    assert "ValidationQuery:" in text
    assert "SearchQuery:" in text
