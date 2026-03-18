# -*- coding: utf-8 -*-

from src.param_validator import ParamValidator


def test_feature_alignment_rejects_bridge_for_conduit_query():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="电气配管",
        candidates=[
            {
                "quota_id": "A",
                "name": "电缆桥架安装",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "镀锌钢管敷设 混凝土结构暗配",
                "rerank_score": 0.20,
                "hybrid_score": 0.20,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    bridge = next(item for item in results if item["quota_id"] == "A")
    assert bridge["param_match"] is False
    assert bridge["feature_alignment_hard_conflict"] is True
    assert bridge["feature_alignment_comparable_count"] > 0


def test_feature_alignment_prefers_wiring_family_for_generic_query():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="配线",
        candidates=[
            {
                "quota_id": "A",
                "name": "镀锌钢管敷设",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "管内穿线 导线敷设",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    conduit = next(item for item in results if item["quota_id"] == "A")
    cable = next(item for item in results if item["quota_id"] == "B")
    assert conduit["feature_alignment_hard_conflict"] is True
    assert cable["feature_alignment_score"] > conduit["feature_alignment_score"]


def test_feature_alignment_rejects_valve_for_pipe_query():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="喷淋钢管",
        candidates=[
            {
                "quota_id": "A",
                "name": "阀门安装",
                "rerank_score": 0.90,
                "hybrid_score": 0.90,
            },
            {
                "quota_id": "B",
                "name": "喷淋钢管安装",
                "rerank_score": 0.15,
                "hybrid_score": 0.15,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    valve = next(item for item in results if item["quota_id"] == "A")
    assert valve["param_match"] is False
    assert valve["feature_alignment_hard_conflict"] is True
