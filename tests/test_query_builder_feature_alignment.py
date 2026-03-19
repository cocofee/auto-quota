from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


def test_build_quota_query_appends_feature_alignment_terms():
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

    query = build_quota_query(
        parser,
        name,
        description,
        specialty="C4",
        bill_params=params,
        canonical_features=canonical_features,
        context_prior=context_prior,
    )

    assert "电缆" in query
    assert "桥架" in query
    assert "3x4" in query
