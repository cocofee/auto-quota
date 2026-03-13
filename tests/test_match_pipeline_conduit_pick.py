from src.match_pipeline import (
    _pick_category_safe_candidate,
    _pick_explicit_plastic_sleeve_candidate,
)


def test_pick_category_safe_candidate_prefers_hidden_pc_conduit_family():
    item = {
        "name": "电气配管 PC25",
        "description": "配置形式:暗配",
    }
    candidates = [
        {"name": "塑料管敷设 刚性阻燃管敷设 砖、混凝土结构明配 外径(mm) 25", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "塑料管敷设 刚性阻燃管敷设 砖、混凝土结构暗配 外径(mm) 25", "param_score": 0.9, "rerank_score": 0.7},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert picked["name"] == "塑料管敷设 刚性阻燃管敷设 砖、混凝土结构暗配 外径(mm) 25"


def test_pick_category_safe_candidate_prefers_sc_conduit_over_explosion_proof_family():
    item = {
        "name": "电气配管 SC20",
        "description": "配置形式:暗配",
    }
    candidates = [
        {"name": "防爆钢管敷设 砖、混凝土结构暗配 公称直径(DN) ≤65", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "镀锌钢管敷设 砖、混凝土结构暗配 公称直径(DN) ≤20", "param_score": 0.8, "rerank_score": 0.7},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert picked["name"] == "镀锌钢管敷设 砖、混凝土结构暗配 公称直径(DN) ≤20"


def test_pick_category_safe_candidate_does_not_force_electrical_family_for_plumbing_sc_code():
    item = {
        "name": "焊接钢管",
        "description": "材质、规格:SC32",
    }
    candidates = [
        {"name": "给排水管道 室内镀锌钢管(螺纹连接) 公称直径(mm以内) 32", "param_score": 0.8, "rerank_score": 0.8},
        {"name": "镀锌钢管敷设 砖、混凝土结构暗配 公称直径(DN) ≤32", "param_score": 0.7, "rerank_score": 0.7},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert picked["name"] == "给排水管道 室内镀锌钢管(螺纹连接) 公称直径(mm以内) 32"


def test_pick_category_safe_candidate_can_promote_exact_conduit_family_beyond_top5():
    item = {
        "name": "电气配管 SC25",
        "description": "配置形式:暗配",
    }
    candidates = [
        {"name": "防爆钢管敷设 砖、混凝土结构暗配 公称直径(DN) ≤65", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "钢管敷设 砖、混凝土结构暗配 公称直径(DN) ≤32", "param_score": 0.8, "rerank_score": 0.8},
        {"name": "钢管敷设 砖、混凝土结构暗配 公称直径(DN) ≤40", "param_score": 0.7, "rerank_score": 0.7},
        {"name": "钢管敷设 砖、混凝土结构暗配 公称直径(DN) ≤50", "param_score": 0.6, "rerank_score": 0.6},
        {"name": "钢管敷设 砖、混凝土结构暗配 公称直径(DN) ≤65", "param_score": 0.5, "rerank_score": 0.5},
        {"name": "镀锌钢管敷设 砖、混凝土结构暗配 公称直径(DN) ≤25", "param_score": 0.4, "rerank_score": 0.4},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert picked["name"] == "镀锌钢管敷设 砖、混凝土结构暗配 公称直径(DN) ≤25"


def test_pick_category_safe_candidate_prefers_plastic_sleeve_family_for_pvc_sleeve():
    item = {
        "name": "PVC塑料套管DN100",
        "description": "材质：钢管/钢板 工作内容：套管制作、安装、套管内封堵",
    }
    candidates = [
        {"name": "一般钢套管制作安装 介质管道公称直径(mm以内) 125", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "一般塑料套管制作安装 介质管道公称直径(mm以内) 100", "param_score": 0.8, "rerank_score": 0.7},
    ]

    picked = _pick_category_safe_candidate(item, candidates)

    assert picked["name"] == "一般塑料套管制作安装 介质管道公称直径(mm以内) 100"


def test_pick_explicit_plastic_sleeve_candidate_skips_non_sleeve_items():
    bill_text = "给排水管道 材质:PVC 规格:DN100"
    candidates = [
        {"name": "一般钢套管制作安装 介质管道公称直径(mm以内) 125", "param_score": 0.9, "rerank_score": 0.9},
        {"name": "给排水管道 室内塑料排水管(粘接) 公称外径(mm以内) 110", "param_score": 0.8, "rerank_score": 0.7},
    ]

    picked = _pick_explicit_plastic_sleeve_candidate(bill_text, candidates)

    assert picked is None
