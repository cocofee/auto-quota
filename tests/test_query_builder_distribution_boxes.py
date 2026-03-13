from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


def test_build_quota_query_uses_distribution_box_template_with_half_perimeter_bucket():
    query = build_quota_query(
        parser,
        "配电箱",
        "规格:350X450X120//安装方式:暗装，距地1.4m",
    )

    assert query == "成套配电箱安装 悬挂、嵌入式 半周长1.0m"


def test_build_quota_query_uses_distribution_box_floor_template():
    query = build_quota_query(
        parser,
        "配电箱",
        "安装方式:落地式",
    )

    assert query == "成套配电箱安装 落地式"


def test_build_quota_query_prefers_specific_distribution_box_name_and_model():
    query = build_quota_query(
        parser,
        "配电箱",
        "名称:普通照明配电箱//型号:1AL2//安装方式:底边距地1.5米明挂",
    )

    assert query == "普通照明配电箱 1AL2"


def test_build_quota_query_splits_attached_distribution_box_model():
    query = build_quota_query(
        parser,
        "配电箱",
        "规格:非标//名称:空调插座配电箱2ALkt1//安装方式:挂墙明装 距地1.5m",
    )

    assert query == "空调插座配电箱 2ALkt1"


def test_build_quota_query_keeps_explicit_distribution_cabinet_family_and_model():
    query = build_quota_query(
        parser,
        "配电箱",
        "成套配电柜安装 ALzm1 2、箱内电气元件配置按系统图要求,防护等级不低于IP55",
    )

    assert query == "成套配电柜安装 ALzm1"
