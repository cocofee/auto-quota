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

    assert results[0]["quota_id"] == "C11-1-1"
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
