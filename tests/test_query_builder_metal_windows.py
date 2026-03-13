from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


def test_build_quota_query_normalizes_metal_window_to_aluminum_window_type():
    name = "金属（塑钢、断桥）窗"
    description = (
        "铝合金平开窗PC1718//"
        "普通铝合金型材,粉末喷涂//"
        "含防坠落装置及窗五金配件等"
    )

    query = build_quota_query(parser, name, description)

    assert query == "铝合金平开窗"


def test_build_quota_query_keeps_attached_frame_flag_for_metal_window():
    name = "金属（塑钢、断桥）窗"
    description = (
        "铝合金平开窗//"
        "框、扇材质:铝合金//"
        "有附框"
    )

    query = build_quota_query(parser, name, description)

    assert query == "铝合金平开窗 有附框"


def test_build_quota_query_skips_pipe_route_for_metal_window_hardware_material_noise():
    name = "金属（塑钢、断桥）窗"
    description = (
        "PC0926//"
        "框、扇材质:普通铝合金型材(粉末喷涂)、50系列平开窗//"
        "具体做法详见门窗深化设计图//"
        "门窗小五金:两点锁、不锈钢专用滑撑、防坠落装置"
    )

    query = build_quota_query(parser, name, description)

    assert query == "铝合金平开窗"


def test_build_quota_query_keeps_pipe_route_for_valve_items_with_men_character():
    name = "闸阀 DN100 类型:闸阀"
    description = "材质:铸铁铜芯 规格、压力等级:DN100 1.6MPa 包括法兰及相关配件供应和安装"

    query = build_quota_query(parser, name, description)

    assert query == "法兰阀门安装 DN100"
