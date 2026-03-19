from src.match_pipeline import _build_item_context
from src.text_parser import TextParser


parser = TextParser()


def test_build_item_context_propagates_canonical_features():
    name = "电力电缆敷设"
    description = "WDZN-BYJ 3x4+2x2.5"
    params = parser.parse(f"{name} {description}")
    context_prior = {"context_hints": ["桥架"]}
    canonical_features = parser.parse_canonical(
        f"{name} {description}",
        specialty="C4",
        context_prior=context_prior,
        params=params,
    )

    context = _build_item_context({
        "name": name,
        "description": description,
        "section": "电气工程",
        "specialty": "C4",
        "params": params,
        "context_prior": context_prior,
        "canonical_features": canonical_features,
    })

    assert context["canonical_features"]["entity"] == "电缆"
    assert "电缆" in context["search_query"]
    assert "桥架" in context["search_query"]
    assert "电缆" in context["full_query"]
    assert context["query_route"]["route"] == "installation_spec"
