# -*- coding: utf-8 -*-

from src.query_builder import _apply_synonyms


def test_apply_synonyms_adds_control_cable_alias_for_kl():
    query = _apply_synonyms("KL 14芯", "C4")

    assert "KL" in query
    assert "控制电缆敷设" in query


def test_apply_synonyms_adds_ground_bus_alias():
    query = _apply_synonyms("接地母线 40*4", "C4")

    assert "接地母线" in query
    assert "接地母线敷设" in query


def test_apply_synonyms_adds_pc_slab_alias():
    query = _apply_synonyms("PC叠合楼板", "A1")

    assert "PC叠合楼板" in query
    assert "装配式混凝土构件 叠合板" in query
