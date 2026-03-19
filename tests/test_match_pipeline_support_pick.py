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
