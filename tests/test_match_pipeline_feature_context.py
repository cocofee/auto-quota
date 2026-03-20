from src.match_pipeline import _build_classification, _build_item_context
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


def test_build_item_context_backfills_params_for_openclaw_items():
    item = {
        "name": "管道支架",
        "description": "1.材质:管道支架\n2.管架形式:按需制作\n3.防腐油漆:除锈后刷防锈漆二道,再刷灰色调和漆二道",
        "section": "给排水管道",
        "specialty": "C10",
    }

    context = _build_item_context(item)

    assert item["params"]["support_scope"] == "管道支架"
    assert item["params"]["support_action"] == "制作"
    assert context["canonical_features"]["family"] == "pipe_support"
    assert "管道支架制作安装" in context["search_query"]


def test_build_classification_backfills_borrow_priority_when_specialty_exists():
    classification = _build_classification(
        {"specialty": "C10"},
        name="管道支架",
        desc="",
        section="给排水管道",
    )

    assert classification["primary"] == "C10"
    assert classification["confidence"] == "high"
    assert classification["fallbacks"][:3] == ["C9", "C8", "C13"]
