from src.bill_cleaner import clean_bill_items
from src.text_parser import TextParser


def test_text_parser_extracts_complex_cable_bundle():
    parser = TextParser()
    result = parser.parse("WDZN-BYJ 3x4+2x2.5 配线")
    assert result["cable_section"] == 4
    assert result["cable_bundle"] == [
        {"cores": 3, "section": 4.0, "role": "main"},
        {"cores": 2, "section": 2.5, "role": "aux"},
    ]


def test_parse_canonical_includes_context_prior():
    parser = TextParser()
    features = parser.parse_canonical(
        "WDZN-BYJ 3x4+2x2.5 配线",
        specialty="C4",
        context_prior={"specialty": "C4", "context_hints": ["桥架"]},
    )
    assert features["specialty"] == "C4"
    assert features["entity"] == "电缆"
    assert features["cable_section"] == 4
    assert features["context_prior"]["context_hints"] == ["桥架"]


def test_clean_bill_items_attaches_context_prior_and_canonical_features():
    items = clean_bill_items([
        {
            "name": "配线",
            "description": "WDZN-BYJ 3x4+2x2.5",
            "section": "电气工程",
            "sheet_name": "安装",
        }
    ])
    item = items[0]
    assert item["context_prior"]["specialty"] == item.get("specialty", "")
    assert item["canonical_features"]["cable_section"] == 4
