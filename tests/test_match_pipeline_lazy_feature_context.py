# -*- coding: utf-8 -*-

from src.match_pipeline import _build_item_context


def test_build_item_context_lazily_restores_feature_context():
    item = {
        "name": "铸铁管",
        "description": "安装部位:室内 介质:污水、废水 材质、规格:机制铸铁管 Dn80 连接形式:机械接口",
        "unit": "m",
        "quantity": 12,
        "specialty": "C10",
        "section": "给排水管道",
        "sheet_name": "表-05 分部分项工程量清单与计价表",
    }

    ctx = _build_item_context(item)

    assert item["context_prior"]["specialty"] == "C10"
    assert item["canonical_features"]["system"] == "给排水"
    assert item["canonical_features"]["material"] == "铸铁管"
    assert ctx["canonical_features"]["system"] == "给排水"
