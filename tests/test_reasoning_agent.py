from src.reasoning_agent import ReasoningAgent
from src.agent_matcher import AgentMatcher


def test_reasoning_agent_extracts_material_and_connection_conflicts():
    packet = ReasoningAgent().build_packet(
        {"name": "镀锌钢管DN100", "query_route": {"route": "installation_spec"}},
        [
            {
                "quota_id": "Q1",
                "name": "镀锌钢管丝接安装",
                "param_score": 0.92,
                "rerank_score": 0.91,
                "candidate_canonical_features": {
                    "entity": "管道",
                    "material": "镀锌钢管",
                    "connection": "丝接",
                    "numeric_params": {"dn": "100"},
                },
            },
            {
                "quota_id": "Q2",
                "name": "不锈钢管法兰安装",
                "param_score": 0.90,
                "rerank_score": 0.89,
                "candidate_canonical_features": {
                    "entity": "管道",
                    "material": "不锈钢",
                    "connection": "法兰",
                    "numeric_params": {"dn": "125"},
                },
            },
        ],
        route_profile={"route": "installation_spec"},
    )

    assert packet["engaged"] is True
    assert "material" in packet["conflict_fields"]
    assert "connection" in packet["conflict_fields"]
    assert any("材质冲突" in text for text in packet["conflict_summaries"])
    assert any("连接冲突" in text for text in packet["conflict_summaries"])


def test_agent_prompt_includes_reasoning_packet():
    matcher = AgentMatcher.__new__(AgentMatcher)
    matcher.province = "test-province"
    matcher.llm_type = "deepseek"
    matcher._client = None

    prompt = matcher._build_agent_prompt(
        {"name": "镀锌钢管DN100", "description": "", "unit": "m"},
        [{"quota_id": "Q1", "name": "镀锌钢管丝接安装", "param_score": 0.9}],
        reasoning_packet={
            "engaged": True,
            "conflict_summaries": ["材质冲突: 镀锌钢管 / 不锈钢"],
            "compare_points": ["优先核对材质是否一致，材质不一致直接排除。"],
        },
    )

    assert "候选差异仲裁摘要" in prompt
    assert "材质冲突" in prompt
    assert "优先核对材质是否一致" in prompt


def test_reasoning_agent_packet_exposes_review_requirement():
    packet = ReasoningAgent().build_packet(
        {"name": "支架", "query_route": {"route": "ambiguous_short"}},
        [
            {
                "quota_id": "Q1",
                "name": "桥架支撑架安装",
                "param_match": True,
                "param_score": 0.90,
                "rerank_score": 0.88,
                "candidate_canonical_features": {"entity": "支架"},
            },
            {
                "quota_id": "Q2",
                "name": "管道支架安装",
                "param_match": True,
                "param_score": 0.89,
                "rerank_score": 0.84,
                "candidate_canonical_features": {"entity": "支架"},
            },
        ],
        route_profile={"route": "ambiguous_short"},
    )

    assert packet["decision"]["route"] == "ambiguous_short"
    assert packet["decision"]["require_final_review"] is True
