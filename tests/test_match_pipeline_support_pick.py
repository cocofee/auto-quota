from src.match_pipeline import _pick_explicit_support_family_candidate


def test_pick_explicit_support_family_candidate_skips_generic_support_without_context():
    picked = _pick_explicit_support_family_candidate(
        "支架",
        [
            {"name": "管道支架制作安装", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "电缆桥架支架制作安装", "param_score": 0.8, "rerank_score": 0.8},
        ],
    )

    assert picked is None


def test_pick_explicit_support_family_candidate_skips_surface_process_quota():
    picked = _pick_explicit_support_family_candidate(
        "管道支架",
        [
            {"name": "管道除锈 手工除锈", "param_score": 0.95, "rerank_score": 0.95},
            {"name": "管道支架制作 单件重量(kg以内) 5", "param_score": 0.6, "rerank_score": 0.6},
        ],
    )

    assert picked["name"] == "管道支架制作 单件重量(kg以内) 5"


def test_pick_explicit_support_family_candidate_prefers_general_pipe_support_over_special_shape():
    picked = _pick_explicit_support_family_candidate(
        "管道支架 1.材质:管道支架 2.管架形式:按需制作 3.防腐油漆:除锈后刷防锈漆二道,再刷灰色调和漆二道",
        [
            {"name": "管道支吊架制作与安装 木垫式管架", "param_score": 0.92, "rerank_score": 0.92},
            {"name": "管道支吊架制作与安装 弹簧式管架", "param_score": 0.90, "rerank_score": 0.90},
            {"name": "室内管道管道支架制作安装 一般管架", "param_score": 0.80, "rerank_score": 0.80},
        ],
    )

    assert picked["name"] == "室内管道管道支架制作安装 一般管架"
