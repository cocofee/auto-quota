# -*- coding: utf-8 -*-

from tools.keyword_miss_query_rewrite_eval import (
    parse_structured_fields,
    rewrite_keyword_miss_query,
    split_noise_segment,
)


def test_split_noise_segment_cuts_after_include_marker():
    primary, noise, marker = split_noise_segment(
        "钢塑复合管 DN50 螺纹连接 综合单价中含穿非混凝土构件的套管制作及安装"
    )

    assert primary == "钢塑复合管 DN50 螺纹连接"
    assert "套管制作及安装" in noise
    assert marker == "综合单价中含"


def test_rewrite_keyword_miss_query_keeps_bridge_core_entity():
    payload = rewrite_keyword_miss_query(
        bill_name="",
        bill_text="强电桥架 600mm×200mm 含穿墙及穿楼板防火封堵 工作内容：采购并安装",
        old_query="堵洞 穿墙 穿楼板 桥架",
    )

    assert payload["new_query"] == "强电桥架 600mm×200mm"
    assert payload["noise_marker"] == "含"


def test_parse_structured_fields_extracts_decisive_fields():
    fields = parse_structured_fields(
        "名称:镀锌钢管 规格:DN100 连接形式:螺纹连接 工作内容:管道水冲洗 其他说明:满足规范"
    )

    assert fields["名称"] == "镀锌钢管"
    assert fields["规格"] == "DN100"
    assert fields["连接形式"] == "螺纹连接"


def test_rewrite_keyword_miss_query_uses_fields_and_drops_noise_fields():
    payload = rewrite_keyword_miss_query(
        bill_name="",
        bill_text=(
            "名称:镀锌钢管 规格:DN100 连接形式:螺纹连接 介质:压力排水 "
            "工作内容:管道水冲洗 其他说明:综合考虑完成该工艺"
        ),
        old_query="管道水冲洗 镀锌钢管",
    )

    assert payload["strategy"] == "fields"
    assert "工作内容" not in payload["new_query"]
    assert "DN100" in payload["new_query"]
    assert "螺纹连接" in payload["new_query"]
    assert "压力排水" in payload["new_query"]


def test_rewrite_keyword_miss_query_prefers_front_segment_over_support_tail():
    payload = rewrite_keyword_miss_query(
        bill_name="",
        bill_text="冷媒管 去磷无缝紫铜管φ9.52 焊接连接 室内敷设 管道支架制作安装",
        old_query="管道支架制作安装 一般管架",
    )

    assert "冷媒管" in payload["new_query"]
    assert "φ9.52" in payload["new_query"]
    assert "支架制作安装" not in payload["new_query"]
