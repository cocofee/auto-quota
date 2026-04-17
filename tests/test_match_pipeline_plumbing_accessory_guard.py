from src.match_pipeline import _pick_category_safe_candidate


def test_pick_category_safe_candidate_keeps_top_pipe_run_when_accessory_signal_is_weaker():
    item = {
        "name": "UPVC排水管",
        "description": "规格:DN100，含管卡、管件（弯头、存水弯、清扫口等）",
    }
    candidates = [
        {"name": "室内塑料排水管 承插连接 公称直径(mm以内) 100", "param_score": 0.92, "rerank_score": 0.93},
        {"name": "清扫口安装 公称直径(mm以内) 100", "param_score": 0.52, "rerank_score": 0.51},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert "排水管" in picked["name"]
