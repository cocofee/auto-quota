from src.match_core import _append_trace_step
from src.match_engine import _move_trace_stage_to_tail
from src.match_pipeline import _resolve_search_mode_result


def _candidate(
    quota_id: str,
    name: str,
    *,
    param_score: float,
    rerank_score: float,
    unit: str = "个",
) -> dict:
    return {
        "quota_id": quota_id,
        "name": name,
        "unit": unit,
        "param_match": True,
        "param_score": param_score,
        "param_detail": f"{quota_id} detail",
        "rerank_score": rerank_score,
    }


def test_resolve_search_mode_result_appends_five_rank_stage_steps_at_trace_tail():
    result, _, _ = _resolve_search_mode_result(
        {
            "name": "配电箱",
            "description": "安装方式:明装 规格:600*900*220 8回路",
            "query_route": {"route": "installation_spec"},
        },
        [
            _candidate("AH-J1", "接线箱明装 半周长(mm以内) 1500", param_score=0.99, rerank_score=0.99),
            _candidate("AH-B1", "成套配电箱安装 悬挂、嵌入式 半周长1.5m 规格(回路以内) 8", param_score=0.72, rerank_score=0.52, unit="台"),
        ],
        exp_backup={},
        rule_backup={},
        exp_hits=0,
        rule_hits=0,
    )

    rank_steps = [step for step in result["trace"]["steps"] if step.get("stage") == "rank_stage"]
    assert len(rank_steps) == 5
    assert [step["name"] for step in result["trace"]["steps"][-5:]] == [
        "ltr",
        "cgr_ranker",
        "candidate_arbiter",
        "explicit_picker",
        "category_safe",
    ]


def test_move_trace_stage_to_tail_reorders_existing_rank_stage_steps():
    result = {
        "trace": {
            "steps": [
                {"stage": "search_select"},
                {"stage": "rank_stage", "name": "ltr"},
                {"stage": "performance_monitor"},
                {"stage": "rank_stage", "name": "cgr_ranker"},
                {"stage": "consistency_review"},
                {"stage": "rank_stage", "name": "candidate_arbiter"},
                {"stage": "final_validate"},
                {"stage": "rank_stage", "name": "explicit_picker"},
                {"stage": "rank_stage", "name": "category_safe"},
            ],
            "path": [
                "search_select",
                "rank_stage",
                "performance_monitor",
                "rank_stage",
                "consistency_review",
                "rank_stage",
                "final_validate",
                "rank_stage",
                "rank_stage",
            ],
        }
    }

    _append_trace_step(result, "postcheck", marker=True)
    _move_trace_stage_to_tail(result, "rank_stage")

    tail = result["trace"]["steps"][-5:]
    assert [step["stage"] for step in tail] == ["rank_stage"] * 5
    assert [step["name"] for step in tail] == [
        "ltr",
        "cgr_ranker",
        "candidate_arbiter",
        "explicit_picker",
        "category_safe",
    ]
    assert result["trace"]["path"][-5:] == ["rank_stage"] * 5
