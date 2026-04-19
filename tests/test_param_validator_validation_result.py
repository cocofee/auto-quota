from src.bill_item_context import BillItemContext
from src.param_validator import ParamValidator


def test_soft_penalty_keeps_match_true_but_drops_score():
    validator = ParamValidator()

    is_match, score, detail = validator._check_params(
        {"weight_t": 10},
        {"weight_t": 5},
    )
    structured = validator._check_params_result(
        {"weight_t": 10},
        {"weight_t": 5},
    )

    assert is_match is True
    assert score < 0.5
    assert "\u91cd\u91cf" in detail
    assert structured.tier == "hard_pass"
    assert structured.hard_signals["structured_params"] == "pass"
    assert structured.soft_score == score


def test_dn_over_bucket_is_still_hard_fail():
    validator = ParamValidator()

    is_match, score, _ = validator._check_params(
        {"dn": 150},
        {"dn": 100},
    )
    structured = validator._check_params_result(
        {"dn": 150},
        {"dn": 100},
    )

    assert is_match is False
    assert score == 0.0
    assert structured.tier == "hard_fail"
    assert structured.hard_signals["structured_params"] == "fail"


def test_validate_candidates_exposes_structured_param_validation():
    validator = ParamValidator()

    results = validator.validate_candidates(
        query_text="\u7ba1\u9053 DN100",
        candidates=[
            {
                "quota_id": "A",
                "name": "\u7ba1\u9053\u5b89\u88c5 \u516c\u79f0\u76f4\u5f84(mm\u4ee5\u5185) 100",
                "rerank_score": 0.9,
                "hybrid_score": 0.9,
            }
        ],
        bill_params={"dn": 100},
    )

    candidate = results[0]
    assert candidate["param_match"] is True
    assert isinstance(candidate["param_validation"], dict)
    assert candidate["param_validation"]["soft_score"] == candidate["param_score"]
    assert candidate["param_validation"]["tier"] in {
        "hard_pass",
        "soft_match",
    }


def test_negative_keyword_hard_conflict_still_rejects_candidate():
    validator = ParamValidator()

    results = validator.validate_candidates(
        query_text="\u666e\u901a\u63d2\u5ea7\u5b89\u88c5",
        candidates=[
            {
                "quota_id": "A",
                "name": "\u9632\u7206\u63d2\u5ea7\u5b89\u88c5",
                "rerank_score": 0.9,
                "hybrid_score": 0.9,
            }
        ],
    )

    candidate = results[0]
    assert candidate["param_match"] is False
    assert candidate["param_tier"] == 0
    assert candidate["param_validation"]["tier"] == "hard_fail"
    assert candidate["param_validation"]["hard_signals"]["negative_keywords"] == "fail"


def test_category_conflict_hard_signal_still_rejects_candidate():
    validator = ParamValidator()

    results = validator.validate_candidates(
        query_text="\u9600\u95e8\u5b89\u88c5 DN100",
        candidates=[
            {
                "quota_id": "A",
                "name": "\u5f2f\u5934\u5b89\u88c5 DN100",
                "rerank_score": 0.9,
                "hybrid_score": 0.9,
            }
        ],
    )

    candidate = results[0]
    assert candidate["param_match"] is False
    assert candidate["param_tier"] == 0
    assert candidate["param_validation"]["tier"] == "hard_fail"
    assert candidate["param_validation"]["hard_signals"]["category_conflict"] == "fail"


def test_validate_candidates_still_backfills_missing_params_from_supplement_query(monkeypatch):
    validator = ParamValidator()

    def fake_parse(text: str):
        if text == "BV4":
            return {}
        if text == "管内穿铜芯线 导线截面 4":
            return {"cable_section": 4}
        if " 4" in text or "≤4" in text:
            return {"cable_section": 4}
        if "2.5" in text:
            return {"cable_section": 2.5}
        return {}

    monkeypatch.setattr("src.param_validator.text_parser.parse", fake_parse)

    bill_item_context = BillItemContext(
        raw_name="BV4",
        raw_desc="",
        params={},
        canonical_query={
            "validation_query": "BV4",
            "search_query": "管内穿铜芯线 导线截面 4",
        },
    )

    results = validator.validate_candidates(
        query_text="BV4",
        supplement_query="管内穿铜芯线 导线截面 4",
        bill_item_context=bill_item_context,
        candidates=[
            {
                "quota_id": "A",
                "name": "管内穿线 穿照明线 铜芯 导线截面(mm2以内) 2.5",
                "rerank_score": 0.9,
                "hybrid_score": 0.9,
            },
            {
                "quota_id": "B",
                "name": "管内穿线 穿照明线 铜芯 导线截面(mm2以内) 4",
                "rerank_score": 0.8,
                "hybrid_score": 0.8,
            },
        ],
    )

    by_id = {candidate["quota_id"]: candidate for candidate in results}
    assert by_id["A"]["param_match"] is False
    assert by_id["A"]["param_tier"] == 0
    assert by_id["B"]["param_match"] is True
    assert by_id["B"]["param_score"] > by_id["A"]["param_score"]
