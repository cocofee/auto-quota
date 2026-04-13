from src.param_validator import ParamValidator
from src.output_writer import OutputWriter
from src.province_book_mapper import map_db_book_to_route_book, map_route_book_to_db_books
from src.query_builder import build_quota_query
from src.specialty_classifier import classify
from src.text_parser import TextParser
from src.match_pipeline import _build_classification


parser = TextParser()


def test_industrial_elbow_does_not_route_to_hvac():
    result = classify(
        "不锈钢无缝弯头",
        "规格:DN100 介质:水 连接形式:氩弧焊",
        bill_code="030804003004",
    )

    assert result["primary"] == "C8"


def test_manual_butterfly_valve_does_not_route_to_hvac():
    result = classify(
        "手动蝶阀",
        "规格:DN100 介质:水 连接形式:法兰",
        bill_code="030807003017",
    )

    assert result["primary"] == "C8"


def test_y_filter_query_adds_deslagger_alias_for_anhui_style_books():
    query = build_quota_query(parser, "Y型过滤器", "规格:DN100 连接形式:法兰")

    assert "Y型过滤器安装" in query
    assert "除污器组成安装" in query


def test_blind_plate_rejects_plain_flange_candidate():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="平焊法兰盲板 DN100 PN16",
        candidates=[
            {
                "quota_id": "A",
                "name": "低压法兰 不锈钢平焊法兰 公称直径(mm以内) 100",
                "rerank_score": 0.96,
                "hybrid_score": 0.96,
            },
            {
                "quota_id": "B",
                "name": "平焊法兰盲板 公称直径(mm以内) 100",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    wrong = next(item for item in results if item["quota_id"] == "A")
    assert wrong["feature_alignment_hard_conflict"] is True
    assert "盲板" in wrong["feature_alignment_detail"]


def test_stainless_protection_layer_query_anchors_to_c12_installation_terms():
    query = build_quota_query(
        parser,
        "不锈钢保温外壳",
        "材料:不锈钢钢板保护层 厚度:0.5mm不锈钢板",
        specialty="C12",
    )

    assert "防潮层、保护层安装" in query



def test_anhui_c12_routes_to_a11_book():
    books = map_route_book_to_db_books(
        "C12",
        province="安徽省安装工程计价定额(2018)",
        available_books={"A1", "A10", "A11"},
    )

    assert books == ["A11"]


def test_anhui_a11_maps_back_to_c12_route_book():
    route_book = map_db_book_to_route_book(
        "A11",
        province="安徽省安装工程计价定额(2018)",
    )

    assert route_book == "C12"


def test_pipe_insulation_with_protection_layer_field_keeps_insulation_route():
    query = build_quota_query(
        parser,
        "管道绝热",
        "材质:岩棉 保护层:铝板 规格:DN100",
        specialty="C10",
    )

    assert "管道绝热" in query
    assert "防潮层、保护层安装" not in query


def test_protection_layer_demolition_keeps_demolition_terms():
    query = build_quota_query(
        parser,
        "保温外壳拆除",
        "材料:不锈钢钢板保护层 厚度:0.5mm不锈钢板",
        specialty="C12",
    )

    assert "拆除" in query
    assert "安装" not in query


def test_material_text_falls_back_to_description_when_materials_do_not_render():
    material_text = OutputWriter._get_material_text(
        {
            "bill_item": {
                "description": "名称:球墨铸铁 DN100 1.0MPa",
            },
            "quotas": [
                {
                    "quota_id": "A1-1",
                    "name": "给排水管道安装",
                }
            ],
            "materials": [
                {
                    "material_name": "球墨铸铁",
                    "unit": "m",
                }
            ],
        }
    )

    assert material_text == "球墨铸铁 DN100 1.0MPa"


def test_y_filter_alias_uses_inferred_threaded_connection():
    query = build_quota_query(
        parser,
        "阀门",
        "种类:Y型过滤器 规格:DN25",
        specialty="C10",
    )

    assert "Y型过滤器安装(螺纹连接)" in query
    assert "除污器组成安装(螺纹连接)" in query
    assert "除污器组成安装(法兰连接)" not in query


def test_manual_diefa_query_normalizes_to_valve_installation():
    query = build_quota_query(
        parser,
        "\u624b\u52a8\u789f\u9600",
        "\u89c4\u683c:DN100 \u8fde\u63a5\u5f62\u5f0f:\u6cd5\u5170",
        specialty="C8",
    )

    assert "\u6cd5\u5170\u9600\u95e8\u5b89\u88c5" in query


def test_industrial_manual_valve_keeps_c8_primary_and_adds_c10_search():
    result = classify(
        "\u624b\u52a8\u789f\u9600",
        "\u89c4\u683c:DN100 \u4ecb\u8d28:\u6c34 \u8fde\u63a5\u5f62\u5f0f:\u6cd5\u5170",
        bill_code="030807003017",
    )

    assert result["primary"] == "C8"
    assert "C10" in result["search_books"]


def test_industrial_y_filter_adds_c10_borrow_search():
    result = classify(
        "\u0059\u578b\u8fc7\u6ee4\u5668",
        "\u89c4\u683c:DN100 \u4ecb\u8d28:\u6c34 \u8fde\u63a5\u5f62\u5f0f:\u6cd5\u5170",
        bill_code="030807001003",
    )

    assert result["primary"] == "C8"
    assert "C10" in result["search_books"]


def test_seeded_c8_accessory_scope_keeps_primary_but_retains_c10_search():
    classification = _build_classification(
        {
            "specialty": "C8",
            "specialty_fallbacks": ["C10", "C13", "C12"],
            "code": "030807003017",
            "context_prior": {},
            "canonical_features": {},
        },
        "\u624b\u52a8\u789f\u9600",
        "\u89c4\u683c:DN100 \u4ecb\u8d28:\u6c34 \u8fde\u63a5\u5f62\u5f0f:\u6cd5\u5170",
        section="",
        sheet_name="\u8868-08",
        province="\u5b89\u5fbd\u7701\u5b89\u88c5\u5de5\u7a0b\u8ba1\u4ef7\u5b9a\u989d(2018)",
    )

    assert classification["primary"] == "C8"
    assert "C10" in classification["search_books"]
    assert "C10" in classification["hard_book_constraints"]