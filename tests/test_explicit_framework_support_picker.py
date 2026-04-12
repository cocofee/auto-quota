# -*- coding: utf-8 -*-
from src.explicit_framework_family_pickers import _pick_explicit_support_family_candidate


def test_pick_explicit_support_family_candidate_treats_two_pipe_as_multi_support():
    picked = _pick_explicit_support_family_candidate(
        "水管两管侧向支吊架 型号:GN-SCDN80 抗震支吊架",
        [
            {"name": "成品抗震支架安装 单管侧向支架", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "抗震支吊架 单向支撑 管道系统 多根", "param_score": 0.7, "rerank_score": 0.6},
        ],
    )

    assert "多根" in picked["name"]
