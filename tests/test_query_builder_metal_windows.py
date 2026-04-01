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

    assert query == "铝合金窗 铝合金平开窗"


def test_build_quota_query_keeps_attached_frame_flag_for_metal_window():
    name = "金属（塑钢、断桥）窗"
    description = (
        "铝合金平开窗//"
        "框、扇材质:铝合金//"
        "有附框"
    )

    query = build_quota_query(parser, name, description)

    assert query == "铝合金窗 铝合金平开窗 有附框"


def test_build_quota_query_skips_pipe_route_for_metal_window_hardware_material_noise():
    name = "金属（塑钢、断桥）窗"
    description = (
        "PC0926//"
        "框、扇材质:普通铝合金型材(粉末喷涂)、50系列平开窗//"
        "具体做法详见门窗深化设计图//"
        "门窗小五金:两点锁、不锈钢专用滑撑、防坠落装置"
    )

    query = build_quota_query(parser, name, description)

    assert query == "铝合金窗 铝合金平开窗"


def test_build_quota_query_keeps_pipe_route_for_valve_items_with_men_character():
    name = "闸阀 DN100 类型:闸阀"
    description = "材质:铸铁铜芯 规格、压力等级:DN100 1.6MPa 包括法兰及相关配件供应和安装"

    query = build_quota_query(parser, name, description)

    assert "法兰阀门安装" in query
    assert "DN100" in query


def test_build_quota_query_routes_steel_fire_door_to_fire_door_family():
    name = "钢质防火门"
    description = "门框、扇材质:FM丙0619：600*1900mm//丙级钢制防火门（含门锁，闭门器）"

    query = build_quota_query(parser, name, description)

    assert query == "钢质防火门 丙级 钢质防火、防盗门"


def test_build_quota_query_routes_metal_door_with_fire_door_desc_to_fire_door_family():
    name = "金属（塑钢）门"
    description = "门框、扇材质:乙级钢质防火门FM1221乙//综合 含门锁，闭门器"

    query = build_quota_query(parser, name, description)

    assert query == "钢质防火门 乙级 钢质防火、防盗门"


def test_build_quota_query_drops_size_noise_and_keeps_family_anchors_for_metal_door_with_fixed_window():
    name = "金属（塑钢）门"
    description = (
        "MC7751：7700*5100//"
        "门框、扇材质:隔热型铝合金平开门：3600*2700//"
        "隔热型铝合金固定窗://"
        "玻璃品种、厚度:8高透光单银Low-E+12A+8透明中空钢化玻璃"
    )

    query = build_quota_query(parser, name, description)

    assert query == "铝合金门 铝合金平开门 隔热断桥型材 铝合金固定窗"


def test_build_quota_query_detects_attached_frame_variants_for_metal_window():
    name = "金属（塑钢、断桥）窗"
    description = (
        "框、扇材质:普通铝合金型材(粉末喷涂)、固定窗、平开窗//"
        "玻璃品种、厚度:6高透光单银LOW-E+12A+6透明双钢化中空玻璃//"
        "木塑附框//防坠落装置"
    )

    query = build_quota_query(parser, name, description)

    assert query == "铝合金窗 铝合金平开窗 有附框"


def test_build_quota_query_expands_common_window_variants_when_opening_type_missing():
    name = "金属（塑钢、断桥）窗"
    description = "框、扇材质:铝合金//玻璃品种、厚度:5mm高透光单银Low-E+12mm空气+5mm透明中空玻璃//C0827"

    query = build_quota_query(parser, name, description)

    assert query == "铝合金窗 铝合金固定窗 铝合金平开窗 铝合金推拉窗"
