from src.match_pipeline import _pick_category_safe_candidate


def test_pick_category_safe_candidate_prefers_floor_drain_over_drain_bolt_family():
    item = {
        "name": "方形地漏DN50",
        "description": "",
    }
    candidates = [
        {"name": "方形伸缩器制作安装 公称直径(mm以内) 50", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "多功能地漏安装", "param_score": 0.8, "rerank_score": 0.896},
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
        {"name": "法兰除污器 公称直径(mm以内) 150", "param_score": 0.8, "rerank_score": 0.896},
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
        {"name": "倒流防止器组成安装(螺纹连接不带水表) 公称直径(mm)50", "param_score": 0.8, "rerank_score": 0.896},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert "不带水表" in picked["name"]


def test_pick_category_safe_candidate_prefers_pipe_clamp_over_pipe_family():
    item = {
        "name": "塑料管卡",
        "description": "规格类型:DN50",
    }
    candidates = [
        {"name": "室外塑料排水管(热熔连接) 公称直径(mm以内) 50", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "塑料管卡安装 公称直径(mm以内) 50", "param_score": 0.7, "rerank_score": 0.896},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert "管卡" in picked["name"]


def test_pick_category_safe_candidate_prefers_flexible_joint_over_flange_install():
    item = {
        "name": "软接头",
        "description": "规格类型:DN100 法兰连接",
    }
    candidates = [
        {"name": "螺纹法兰安装 公称直径(mm以内) 100", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "橡胶软接头安装(法兰连接) 公称直径(mm以内) 100", "param_score": 0.7, "rerank_score": 0.896},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert "软接头" in picked["name"]


def test_pick_category_safe_candidate_prefers_horn_family_over_broadcast_speaker():
    item = {
        "name": "铸铁溢水喇叭口",
        "description": "规格类型:DN75",
    }
    candidates = [
        {"name": "广播喇叭及音箱 组合式", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "铸铁溢水喇叭口安装 公称直径(mm以内) 75", "param_score": 0.8, "rerank_score": 0.896},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert "喇叭口" in picked["name"]


def test_pick_category_safe_candidate_prefers_pipe_insulation_over_equipment_insulation():
    item = {
        "name": "管道绝热",
        "description": "绝热材料品种:橡塑管壳 管道外径:DN50以下",
    }
    candidates = [
        {"name": "绝热工程 泡沫塑料板安装 立式设备", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "管道绝热 橡塑管壳安装 公称直径(mm以内) 50", "param_score": 0.8, "rerank_score": 0.896},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert "管道绝热" in picked["name"]
