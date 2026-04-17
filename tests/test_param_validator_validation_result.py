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
