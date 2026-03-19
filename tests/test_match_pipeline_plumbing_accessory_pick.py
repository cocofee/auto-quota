from src.match_pipeline import _pick_category_safe_candidate


def test_pick_category_safe_candidate_prefers_floor_drain_over_drain_bolt_family():
    item = {
        "name": "方形地漏DN50",
        "description": "",
    }
    candidates = [
        {"name": "方形伸缩器制作安装 公称直径(mm以内) 50", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "多功能地漏安装", "param_score": 0.8, "rerank_score": 0.7},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert "地漏" in picked["name"]


def test_pick_category_safe_candidate_prefers_filter_family_over_water_hammer():
    item = {
        "name": "过滤器",
        "description": "规格类型:DN150 法兰连接",
    }
    candidates = [
        {"name": "水锤消除器安装(法兰连接) 公称直径(mm以内) 150", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "法兰除污器 公称直径(mm以内) 150", "param_score": 0.8, "rerank_score": 0.7},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert "除污器" in picked["name"]


def test_pick_category_safe_candidate_prefers_backflow_preventer_without_water_meter():
    item = {
        "name": "倒流防止器",
        "description": "规格类型:DN50 螺纹连接",
    }
    candidates = [
        {"name": "倒流防止器组成安装(螺纹连接带水表) 公称直径(mm)50", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "倒流防止器组成安装(螺纹连接不带水表) 公称直径(mm)50", "param_score": 0.8, "rerank_score": 0.7},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert "不带水表" in picked["name"]
