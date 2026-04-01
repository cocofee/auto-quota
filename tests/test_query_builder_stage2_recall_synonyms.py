# -*- coding: utf-8 -*-

from src.query_builder import _apply_synonyms


def test_apply_synonyms_adds_waterproofing_family_aliases():
    assert "改性沥青自粘卷材自粘法" in _apply_synonyms("墙面卷材防水", "A1")
    assert "改性沥青自粘卷材自粘法" in _apply_synonyms("楼（地）面卷材防水", "A1")
    assert "改性沥青防水涂料" in _apply_synonyms("屋面涂膜防水", "A1")


def test_apply_synonyms_adds_building_finish_aliases():
    assert "楼地面地砖" in _apply_synonyms("块料楼地面", "A1")
    assert "铝合金窗" in _apply_synonyms("金属（塑钢、断桥）窗", "A1")
    assert "铝合金门" in _apply_synonyms("金属（塑钢）门", "A1")


def test_apply_synonyms_adds_panel_light_alias():
    assert "吸顶灯" in _apply_synonyms("平板灯", "C4")


def test_apply_synonyms_adds_garden_finish_and_fire_door_aliases():
    assert "栽植乔木（带土球）" in _apply_synonyms("栽植乔木", "A1")
    assert "栽植灌木（带土球）" in _apply_synonyms("栽植灌木", "A1")
    assert "楼地面地砖踢脚板" in _apply_synonyms("块料踢脚线", "A1")
    assert "钢质防火、防盗门" in _apply_synonyms("钢质防火门", "A1")


def test_apply_synonyms_adds_recall_aliases_for_preembed_and_municipal_terms():
    assert "预埋铁件安装" in _apply_synonyms("预埋铁件", "A1")
    assert "透层、粘层、封层、粘贴卷材" in _apply_synonyms("透层、粘层", "A1")
    assert "交通标志杆制作" in _apply_synonyms("标杆", "A1")
    assert "预埋螺栓" in _apply_synonyms("螺栓", "A1")


def test_apply_synonyms_scopes_general_lamp_alias_to_installation():
    assert "普通灯具安装" in _apply_synonyms("普通灯具", "C4")
    assert "普通灯具安装" not in _apply_synonyms("普通灯具", "A1")
