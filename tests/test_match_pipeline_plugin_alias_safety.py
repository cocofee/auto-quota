# -*- coding: utf-8 -*-

from src.match_pipeline import _build_item_context


def test_build_item_context_keeps_plugin_alias_out_of_plastic_valve_search_query(monkeypatch):
    monkeypatch.setattr(
        "src.match_pipeline.resolve_plugin_hints",
        lambda **kwargs: {
            "matched_terms": ["塑料阀门"],
            "synonym_aliases": ["快速取水阀安装"],
            "preferred_specialties": ["C10"],
            "preferred_books": ["C10"],
        },
    )

    context = _build_item_context({
        "name": "塑料阀门",
        "description": "PPR截止阀 DN32",
        "specialty": "C10",
        "section": "给排水工程",
        "context_prior": {},
        "params": {"dn": 32, "material": "PPR"},
        "canonical_features": {
            "canonical_name": "塑料阀门",
            "entity": "valve",
            "system": "water",
        },
    })

    assert "快速取水阀安装" not in context["search_query"]
    assert context["plugin_hints"]["synonym_aliases"] == ["快速取水阀安装"]


def test_build_item_context_keeps_sleeve_aliases_out_of_blocking_search_query(monkeypatch):
    monkeypatch.setattr(
        "src.match_pipeline.resolve_plugin_hints",
        lambda **kwargs: {
            "matched_terms": ["套管"],
            "synonym_aliases": [
                "一般钢套管制作安装(介质管道公称直径100mm以内)",
                "刚性防水套管制作D159*4.5",
            ],
            "preferred_specialties": ["C10"],
            "preferred_books": ["C10"],
        },
    )

    context = _build_item_context({
        "name": "堵洞",
        "description": "管道穿楼板孔洞封堵",
        "specialty": "C10",
        "section": "给排水工程",
        "context_prior": {},
        "canonical_features": {
            "canonical_name": "堵洞",
            "entity": "blocking",
            "system": "water",
        },
    })

    assert "一般钢套管制作安装" not in context["search_query"]
    assert "刚性防水套管制作" not in context["search_query"]
    assert "堵洞" in context["search_query"]
