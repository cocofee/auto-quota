from src.param_validator import ParamValidator
from src.text_parser import TextParser


def test_extract_install_method_from_structured_field():
    parser = TextParser()

    parsed = parser.parse(
        "\u63d2\u5ea7 \u5b89\u88c5\u65b9\u5f0f:\u6697\u88c5 "
        "\u5de5\u4f5c\u5185\u5bb9:\u672c\u4f53\u5b89\u88c5\u3001\u63a5\u7ebf"
    )

    assert parsed["install_method"] == "\u6697\u88c5"


def test_extract_install_method_ignores_ambiguous_structured_field():
    parser = TextParser()

    parsed = parser.parse(
        "\u5355\u8054\u5355\u63a7\u5f00\u5173 "
        "\u5b89\u88c5\u5f62\u5f0f:\u6697\u88c5/\u660e\u88c5\u7efc\u5408\u8003\u8651"
    )

    assert "install_method" not in parsed


def test_feature_alignment_uses_single_sided_install_method_signal():
    validator = ParamValidator()

    missing_install = validator._score_feature_alignment(
        bill_canonical_features={
            "canonical_name": "\u63a5\u7ebf\u76d2",
            "entity": "\u63a5\u7ebf\u76d2",
            "install_method": "\u6697\u88c5",
        },
        candidate_features={
            "canonical_name": "\u63a5\u7ebf\u76d2",
            "entity": "\u63a5\u7ebf\u76d2",
        },
    )
    exact_install = validator._score_feature_alignment(
        bill_canonical_features={
            "canonical_name": "\u63a5\u7ebf\u76d2",
            "entity": "\u63a5\u7ebf\u76d2",
            "install_method": "\u6697\u88c5",
        },
        candidate_features={
            "canonical_name": "\u63a5\u7ebf\u76d2",
            "entity": "\u63a5\u7ebf\u76d2",
            "install_method": "\u6697\u88c5",
        },
    )

    assert missing_install["comparable_count"] > 0
    assert missing_install["score"] < exact_install["score"]


def test_feature_alignment_penalizes_generic_candidate_when_bill_has_specifics():
    validator = ParamValidator()

    generic_with_specific_bill = validator._score_feature_alignment(
        bill_canonical_features={
            "canonical_name": "\u63a5\u7ebf\u76d2",
            "entity": "\u63a5\u7ebf\u76d2",
            "install_method": "\u6697\u88c5",
        },
        candidate_features={
            "canonical_name": "\u63a5\u7ebf\u76d2",
            "entity": "\u63a5\u7ebf\u76d2",
        },
    )
    generic_with_plain_bill = validator._score_feature_alignment(
        bill_canonical_features={
            "canonical_name": "\u63a5\u7ebf\u76d2",
            "entity": "\u63a5\u7ebf\u76d2",
        },
        candidate_features={
            "canonical_name": "\u63a5\u7ebf\u76d2",
            "entity": "\u63a5\u7ebf\u76d2",
        },
    )

    assert generic_with_specific_bill["score"] < generic_with_plain_bill["score"]
