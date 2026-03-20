from src.match_pipeline import _build_item_context
from src.text_parser import TextParser


parser = TextParser()


def test_build_item_context_propagates_canonical_features():
    name = "\u7535\u529b\u7535\u7f06\u6577\u8bbe"
    description = "WDZN-BYJ 3x4+2x2.5"
    params = parser.parse(f"{name} {description}")
    context_prior = {"context_hints": ["\u6865\u67b6"]}
    canonical_features = parser.parse_canonical(
        f"{name} {description}",
        specialty="C4",
        context_prior=context_prior,
        params=params,
    )

    context = _build_item_context({
        "name": name,
        "description": description,
        "section": "\u7535\u6c14\u5de5\u7a0b",
        "specialty": "C4",
        "params": params,
        "context_prior": context_prior,
        "canonical_features": canonical_features,
    })

    assert context["canonical_features"]["entity"] == "\u7535\u7f06"
    assert "\u7535\u7f06" in context["search_query"]
    assert "\u6865\u67b6" in context["search_query"]
    assert "\u7535\u7f06" in context["full_query"]
    assert context["canonical_query"]["raw_query"] == f"{name} {description}"
    assert context["canonical_query"]["search_query"] == context["search_query"]
    assert context["canonical_query"]["validation_query"] == context["full_query"]
    assert context["canonical_query"]["normalized_query"] == context["normalized_query"]
    assert context["query_route"]["route"] == "installation_spec"
