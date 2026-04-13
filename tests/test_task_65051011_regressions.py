from src.final_validator import FinalValidator
from src.match_pipeline import _build_item_context


def test_build_item_context_backfills_partial_support_params_for_pipe_support_items():
    item = {
        "name": "管道支架制作安装",
        "description": (
            "1.名称:管道支架制作安装\n"
            "2.单件支架质量:20KG\n"
            "3.材质:Q235B\n"
            "4.管架形式:一般管架"
        ),
        "specialty": "C8",
        "section": "工业管道",
        "params": {"weight_t": 0.02},
    }

    context = _build_item_context(item)

    assert item["params"]["support_scope"] == "管道支架"
    assert item["params"]["support_action"] == "制作安装"
    assert context["canonical_features"]["family"] == "pipe_support"
    assert "管道支架制作安装" in context["search_query"]
    assert "一般管架" in context["search_query"]


def test_build_item_context_backfills_pipe_fitting_shape_for_generic_pipe_fitting_items():
    item = {
        "name": "低压碳钢管件",
        "description": "1.材质:碳钢\n2.规格:90°弯头 DN200\n3.连接方式:电弧焊",
        "specialty": "C8",
        "section": "工业管道",
        "params": {"dn": 200},
    }

    context = _build_item_context(item)

    assert item["params"]["material"] == "碳钢"
    assert "弯头" in context["search_query"]
    assert "DN200" in context["search_query"]


def test_build_item_context_backfills_plate_roll_pipe_fitting_shape():
    item = {
        "name": "低压碳钢板卷管件",
        "description": "1.材质:碳钢\n2.规格:三通 DN700\n3.焊接方法:电弧焊",
        "specialty": "C8",
        "section": "工业管道",
        "params": {"dn": 700},
    }

    context = _build_item_context(item)

    assert item["params"]["material"] == "碳钢"
    assert "三通" in context["search_query"]
    assert "DN700" in context["search_query"]


def test_final_validator_marks_skip_measure_results_green():
    result = {
        "bill_item": {"name": "高层施工增加", "description": "", "unit": "项"},
        "quotas": [],
        "confidence": 0,
        "match_source": "skip_measure",
        "reason_tags": ["measure_item", "abstained"],
        "explanation": "措施项（管理费用），不套安装定额",
    }

    FinalValidator(auto_correct=False).validate_result(result)

    assert result["review_risk"] == "low"
    assert result["light_status"] == "green"
    assert result["final_validation"]["status"] == "ok"


