# -*- coding: utf-8 -*-

from src.param_validator import ParamValidator
from src.text_parser import TextParser


parser = TextParser()


def test_context_alignment_prefers_electrical_support_when_bridge_hint_present():
    validator = ParamValidator()
    context_prior = {"specialty": "C4", "context_hints": ["桥架"]}
    canonical_features = parser.parse_canonical(
        "支架",
        specialty="C4",
        context_prior=context_prior,
    )

    results = validator.validate_candidates(
        query_text="支架",
        candidates=[
            {
                "quota_id": "C10-1-1",
                "name": "管道支架制作安装",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "C4-11-1",
                "name": "电缆桥架支架制作安装",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
        canonical_features=canonical_features,
        context_prior=context_prior,
    )

    assert results[0]["quota_id"] == "C4-11-1"
    assert results[0]["context_alignment_score"] > results[1]["context_alignment_score"]


def test_context_rectify_can_override_rerank_for_strong_bridge_context():
    validator = ParamValidator()
    context_prior = {"specialty": "C4", "context_hints": ["桥架"]}
    canonical_features = parser.parse_canonical(
        "支架",
        specialty="C4",
        context_prior=context_prior,
    )

    results = validator.validate_candidates(
        query_text="支架",
        candidates=[
            {
                "quota_id": "C10-1-1",
                "name": "管道支架制作安装",
                "rerank_score": 0.99,
                "hybrid_score": 0.99,
            },
            {
                "quota_id": "C4-11-1",
                "name": "电缆桥架支架制作安装",
                "rerank_score": 0.01,
                "hybrid_score": 0.01,
            },
        ],
        canonical_features=canonical_features,
        context_prior=context_prior,
    )

    assert results[0]["quota_id"] == "C4-11-1"
    assert results[0]["context_alignment_score"] >= 0.78
    assert results[1]["context_alignment_score"] < results[0]["context_alignment_score"]


def test_context_alignment_prefers_fire_pipe_under_fire_specialty():
    validator = ParamValidator()
    context_prior = {"specialty": "C9"}
    canonical_features = parser.parse_canonical(
        "钢管 DN100",
        specialty="C9",
        context_prior=context_prior,
        params={"dn": 100},
    )

    results = validator.validate_candidates(
        query_text="钢管 DN100",
        candidates=[
            {
                "quota_id": "C10-1-1",
                "name": "给水钢管安装 公称直径(mm以内) 100",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "C9-1-1",
                "name": "喷淋钢管安装 公称直径(mm以内) 100",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
        bill_params={"dn": 100},
        canonical_features=canonical_features,
        context_prior=context_prior,
    )

    assert results[0]["quota_id"] == "C9-1-1"
    assert results[0]["context_alignment_score"] > results[1]["context_alignment_score"]


def test_context_alignment_rejects_non_optical_cable_when_context_is_optical():
    validator = ParamValidator()
    context_prior = {"specialty": "C11", "cable_type": "光缆"}
    canonical_features = parser.parse_canonical(
        "配线",
        specialty="C11",
        context_prior=context_prior,
    )

    results = validator.validate_candidates(
        query_text="配线",
        candidates=[
            {
                "quota_id": "C11-1-1",
                "name": "通信光缆敷设",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
            {
                "quota_id": "C4-10-1",
                "name": "电力电缆敷设",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
        ],
        canonical_features=canonical_features,
        context_prior=context_prior,
    )

    assert results[0]["quota_id"] == "C4-10-1"
    wrong_family = next(item for item in results if item["quota_id"] == "C4-10-1")
    assert wrong_family["context_alignment_hard_conflict"] is True
    assert wrong_family["param_match"] is False


def test_candidate_precomputed_canonical_features_are_reused():
    validator = ParamValidator()
    features = validator._build_candidate_canonical_features(
        candidate={
            "quota_id": "C9-1-1",
            "name": "钢管安装",
            "canonical_features": {
                "entity": "管道",
                "system": "消防",
                "material": "喷淋钢管",
                "connection": "螺纹连接",
            },
        },
        merged_quota_params={"dn": 100},
    )

    assert features["system"] == "消防"
    assert features["material"] == "喷淋钢管"
    assert features["connection"] == "螺纹连接"


def test_context_alignment_softens_cross_specialty_when_system_matches():
    validator = ParamValidator()
    context_prior = {"specialty": "C10"}
    canonical_features = parser.parse_canonical(
        "铸铁管 DN80",
        specialty="C10",
        context_prior=context_prior,
        params={"dn": 80},
    )

    results = validator.validate_candidates(
        query_text="铸铁管 DN80",
        candidates=[
            {
                "quota_id": "C2-1-1",
                "name": "给排水管道 室内柔性铸铁排水管(机械接口) DN80",
                "rerank_score": 0.3,
                "hybrid_score": 0.3,
            },
            {
                "quota_id": "C7-1-1",
                "name": "通风空调 风管止回阀 周长(mm以内) 800",
                "rerank_score": 0.3,
                "hybrid_score": 0.3,
            },
        ],
        bill_params={"dn": 80},
        canonical_features=canonical_features,
        context_prior=context_prior,
    )

    same_system = next(item for item in results if item["quota_id"] == "C2-1-1")
    cross_system = next(item for item in results if item["quota_id"] == "C7-1-1")
    assert same_system["context_alignment_score"] > cross_system["context_alignment_score"]
    assert same_system["context_alignment_score"] >= 0.85


def test_feature_alignment_rejects_sleeve_for_pipe_query():
    validator = ParamValidator()
    canonical_features = parser.parse_canonical(
        "PVC-U排水管 De75",
        specialty="C10",
        params={"dn": 75},
    )

    results = validator.validate_candidates(
        query_text="PVC-U排水管 De75",
        candidates=[
            {
                "quota_id": "C10-SLEEVE",
                "name": "刚性防水套管制作安装 DN75",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "C10-PIPE",
                "name": "给排水管道 室内塑料排水管 De75",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
        bill_params={"dn": 75},
        canonical_features=canonical_features,
        context_prior={"specialty": "C10"},
    )

    assert results[0]["quota_id"] == "C10-PIPE"
    wrong = next(item for item in results if item["quota_id"] == "C10-SLEEVE")
    assert wrong["feature_alignment_hard_conflict"] is True


def test_feature_alignment_rejects_valve_accessory_for_pipe_query():
    validator = ParamValidator()
    canonical_features = parser.parse_canonical(
        "给水管 DN50",
        specialty="C10",
        params={"dn": 50},
    )

    results = validator.validate_candidates(
        query_text="给水管 DN50",
        candidates=[
            {
                "quota_id": "C10-METER",
                "name": "螺翼式水表组成安装 公称直径DN50",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "C10-PIPE",
                "name": "给排水管道 室内塑料给水管 De63",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
        bill_params={"dn": 50},
        canonical_features=canonical_features,
        context_prior={"specialty": "C10"},
    )

    assert results[0]["quota_id"] == "C10-PIPE"
    wrong = next(item for item in results if item["quota_id"] == "C10-METER")
    assert wrong["feature_alignment_hard_conflict"] is True


def test_feature_alignment_rejects_sleeve_for_pressure_pipe_query():
    validator = ParamValidator()
    canonical_features = parser.parse_canonical(
        "覆塑不锈钢(PP-R)压力管 DN50【冷水管】",
        specialty="C10",
        params={"dn": 50},
    )

    results = validator.validate_candidates(
        query_text="覆塑不锈钢(PP-R)压力管 DN50【冷水管】",
        candidates=[
            {
                "quota_id": "C10-SLEEVE",
                "name": "一般钢套管制作安装 介质管道 公称直径（mm以内） 65",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "C10-PIPE",
                "name": "给排水管道 室内 塑料给水管（热熔连接） 外径（mm以内） 50",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
        bill_params={"dn": 50},
        canonical_features=canonical_features,
        context_prior={"specialty": "C10"},
    )

    assert results[0]["quota_id"] == "C10-PIPE"
    wrong = next(item for item in results if item["quota_id"] == "C10-SLEEVE")
    assert wrong["feature_alignment_hard_conflict"] is True


def test_feature_alignment_rejects_sleeve_for_drain_pipe_query():
    validator = ParamValidator()
    canonical_features = parser.parse_canonical(
        "HTPP三层复合静音管 De75【污、废水管】",
        specialty="C10",
        params={"dn": 75},
    )

    results = validator.validate_candidates(
        query_text="HTPP三层复合静音管 De75【污、废水管】",
        candidates=[
            {
                "quota_id": "C10-SLEEVE",
                "name": "密闭穿墙管制作、安装 单管 公称直径（mm以内） DN65",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "C10-PIPE",
                "name": "给排水管道 室内 塑料排水管（粘接） 外径（mm以内） 75",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
        bill_params={"dn": 75},
        canonical_features=canonical_features,
        context_prior={"specialty": "C10"},
    )

    assert results[0]["quota_id"] == "C10-PIPE"
    wrong = next(item for item in results if item["quota_id"] == "C10-SLEEVE")
    assert wrong["feature_alignment_hard_conflict"] is True
