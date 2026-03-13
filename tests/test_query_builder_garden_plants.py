from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


def test_build_quota_query_uses_soil_ball_size_for_garden_tree():
    name = "栽植乔木"
    description = (
        "种类:大腹木棉//"
        "起挖方式:带土球起挖//"
        "土球:土球直径200cm以内"
    )

    query = build_quota_query(parser, name, description)

    assert query == "栽植乔木 土球直径200cm以内"


def test_build_quota_query_uses_soil_ball_size_for_garden_shrub():
    name = "栽植灌木"
    description = "土球:100cm//袋苗、冠幅饱满"

    query = build_quota_query(parser, name, description)

    assert query == "栽植灌木 土球直径100cm以内"
