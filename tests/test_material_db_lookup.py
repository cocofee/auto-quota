# -*- coding: utf-8 -*-

from pathlib import Path

import src.material_db as material_db_module
from src.material_db import MaterialDB


def test_search_price_by_name_rejects_spec_boundary_mismatch(tmp_path: Path):
    db = MaterialDB(str(tmp_path / "material.db"))

    material_id = db.add_material("HDPE给水管", "De630", "m")
    db.add_price(
        material_id,
        2483.0,
        "official_info",
        province="广东",
        unit="m",
        period_end="2026-04-01",
    )

    result = db.search_price_by_name(
        "HDPE给水管",
        province="广东",
        spec="De63",
        target_unit="m",
    )

    assert result is None


def test_search_price_by_name_prefers_exact_spec_over_longer_spec(tmp_path: Path):
    db = MaterialDB(str(tmp_path / "material.db"))

    material_id_63 = db.add_material("HDPE给水管", "De63", "m")
    db.add_price(
        material_id_63,
        42.6,
        "official_info",
        province="广东",
        unit="m",
        period_end="2026-04-01",
    )

    material_id_630 = db.add_material("HDPE给水管", "De630", "m")
    db.add_price(
        material_id_630,
        2483.0,
        "official_info",
        province="广东",
        unit="m",
        period_end="2026-04-01",
    )

    result = db.search_price_by_name(
        "HDPE给水管",
        province="广东",
        spec="De63",
        target_unit="m",
    )

    assert result is not None
    assert result["price"] == 42.6
    assert result["matched_spec"] == "De63"


def test_search_price_by_name_preserves_full_spec_on_boundary_fallback(tmp_path: Path):
    db = MaterialDB(str(tmp_path / "material.db"))

    dn_only_id = db.add_material("钢带增强聚乙烯螺旋波纹管", "DN1000", "m")
    db.add_price(
        dn_only_id,
        680.0,
        "official_info",
        province="广东",
        unit="m",
        period_end="2026-04-01",
    )

    rich_spec_id = db.add_material("钢带增强聚乙烯螺旋波纹管", "DN1000 SN8/SN10/SN12.5", "m")
    db.add_price(
        rich_spec_id,
        980.0,
        "official_info",
        province="广东",
        unit="m",
        period_end="2026-04-01",
    )

    result = db.search_price_by_name(
        "钢带增强聚乙烯螺旋波纹管",
        province="广东",
        spec="DN1000SN8/SN10/SN12.5",
        target_unit="m",
    )

    assert result is not None
    assert result["price"] == 980.0
    assert result["matched_spec"] == "DN1000 SN8/SN10/SN12.5"


def test_search_price_by_name_respects_object_type_for_fuzzy_candidates(tmp_path: Path):
    db = MaterialDB(str(tmp_path / "material.db"))

    pipe_id = db.add_material("HDPE给水管", "De63", "m")
    db.add_price(
        pipe_id,
        42.6,
        "official_info",
        province="广东",
        unit="m",
        period_end="2026-04-01",
    )

    fitting_id = db.add_material("HDPE热熔管件", "De63", "个")
    db.add_price(
        fitting_id,
        128.82,
        "official_info",
        province="广东",
        unit="个",
        period_end="2026-04-01",
    )

    result = db.search_price_by_name(
        "HDPE",
        province="广东",
        spec="De63",
        target_unit="m",
        object_type="pipe",
    )

    assert result is not None
    assert result["price"] == 42.6
    assert result["matched_name"] == "HDPE给水管"
    assert result["matched_object_type"] == "pipe"


def test_search_price_by_name_treats_flanged_valves_as_valves_in_fuzzy_lookup(tmp_path: Path):
    db = MaterialDB(str(tmp_path / "material.db"))

    material_id = db.add_material("法兰闸阀", "DN100", "个")
    db.add_price(
        material_id,
        266.0,
        "official_info",
        province="广东",
        unit="个",
        period_end="2026-04-01",
    )

    result = db.search_price_by_name(
        "闸阀",
        province="广东",
        spec="DN100",
        target_unit="个",
        object_type="valve",
    )

    assert result is not None
    assert result["price"] == 266.0
    assert result["matched_name"] == "法兰闸阀"
    assert result["matched_object_type"] == "valve"


def test_search_price_by_name_converts_ton_price_to_meter_for_steel_pipe(tmp_path: Path):
    db = MaterialDB(str(tmp_path / "material.db"))

    material_id = db.add_material("镀锌钢管", "DN32", "m")
    db.add_price(
        material_id,
        5000.0,
        "official_info",
        province="广东",
        unit="吨",
        period_end="2026-04-01",
    )

    result = db.search_price_by_name(
        "镀锌钢管",
        province="广东",
        spec="DN32",
        target_unit="米",
    )

    assert result is not None
    assert result["price"] == 16.53
    assert result["unit"] == "米"
    assert result["matched_spec"] == "DN32"


def test_search_price_by_name_converts_seamless_pipe_by_outer_diameter_and_thickness(tmp_path: Path):
    db = MaterialDB(str(tmp_path / "material.db"))

    material_id = db.add_material("无缝钢管", "Φ108×4", "m")
    db.add_price(
        material_id,
        5000.0,
        "official_info",
        province="广东",
        unit="t",
        period_end="2026-04-01",
    )

    result = db.search_price_by_name(
        "无缝钢管",
        province="广东",
        spec="Φ108×4",
        target_unit="m",
    )

    assert result is not None
    assert result["price"] == 51.29
    assert result["unit"] == "m"
    assert result["matched_spec"] == "Φ108×4"


def test_search_price_by_name_rejects_dn_only_seamless_pipe_ton_to_meter_match(tmp_path: Path):
    db = MaterialDB(str(tmp_path / "material.db"))

    material_id = db.add_material("无缝钢管", "DN100", "m")
    db.add_price(
        material_id,
        5000.0,
        "official_info",
        province="广东",
        unit="t",
        period_end="2026-04-01",
    )

    result = db.search_price_by_name(
        "无缝钢管",
        province="广东",
        spec="DN100",
        target_unit="m",
    )

    assert result is None


def test_search_price_by_name_rejects_non_convertible_ton_to_meter_match(tmp_path: Path):
    db = MaterialDB(str(tmp_path / "material.db"))

    material_id = db.add_material("钢板", "δ6", "t")
    db.add_price(
        material_id,
        4200.0,
        "official_info",
        province="广东",
        unit="t",
        period_end="2026-04-01",
    )

    result = db.search_price_by_name(
        "钢板",
        province="广东",
        spec="δ6",
        target_unit="m",
    )

    assert result is None


def test_search_price_by_name_rejects_incompatible_alias_for_lined_steel_pipe(tmp_path: Path, monkeypatch):
    db = MaterialDB(str(tmp_path / "material.db"))

    material_id = db.add_material("镀锌钢管", "DN100", "m")
    db.add_price(
        material_id,
        32.5,
        "official_info",
        province="广东",
        unit="m",
        period_end="2026-04-01",
    )

    monkeypatch.setattr(material_db_module, "_get_material_alias", lambda _name: "镀锌钢管")

    result = db.search_price_by_name(
        "衬塑钢管",
        province="广东",
        spec="DN100",
        target_unit="m",
    )

    assert result is None


def test_search_price_by_name_prefers_lined_steel_pipe_over_galvanized_pipe(tmp_path: Path):
    db = MaterialDB(str(tmp_path / "material.db"))

    galvanized_id = db.add_material("镀锌钢管", "DN100", "m")
    db.add_price(
        galvanized_id,
        32.5,
        "official_info",
        province="广东",
        unit="m",
        period_end="2026-04-01",
    )

    lined_id = db.add_material("衬塑钢管", "DN100", "m")
    db.add_price(
        lined_id,
        58.0,
        "official_info",
        province="广东",
        unit="m",
        period_end="2026-04-01",
    )

    result = db.search_price_by_name(
        "衬塑钢管",
        province="广东",
        spec="DN100",
        target_unit="m",
    )

    assert result is not None
    assert result["price"] == 58.0
    assert result["matched_name"] == "衬塑钢管"


def test_search_price_by_name_prefers_exact_city_and_period(tmp_path: Path):
    db = MaterialDB(str(tmp_path / "material.db"))

    material_id = db.add_material("焊接钢管", "DN80", "m")
    db.add_price(
        material_id,
        28.04,
        "official_info",
        province="江西",
        city="九江",
        unit="m",
        period_end="2025-08-31",
    )
    db.add_price(
        material_id,
        33.33,
        "official_info",
        province="江西",
        city="南昌",
        unit="m",
        period_end="2025-08-31",
    )
    db.add_price(
        material_id,
        40.00,
        "official_info",
        province="江西",
        city="九江",
        unit="m",
        period_end="2025-09-30",
    )

    result = db.search_price_by_name(
        "焊接钢管",
        province="江西",
        city="九江",
        period_end="2025-08-31",
        spec="DN80",
        target_unit="m",
    )

    assert result is not None
    assert result["price"] == 28.04
    assert result["source"] == "江西九江信息价(2025-08-31)"


def test_search_price_by_name_prefers_selected_period_before_latest_city(tmp_path: Path):
    db = MaterialDB(str(tmp_path / "material.db"))

    material_id = db.add_material("焊接钢管", "DN70", "m")
    db.add_price(
        material_id,
        27.77,
        "official_info",
        province="江西",
        city="",
        unit="m",
        period_end="2025-08-31",
    )
    db.add_price(
        material_id,
        35.00,
        "official_info",
        province="江西",
        city="九江",
        unit="m",
        period_end="2025-09-30",
    )

    result = db.search_price_by_name(
        "焊接钢管",
        province="江西",
        city="九江",
        period_end="2025-08-31",
        spec="DN70",
        target_unit="m",
    )

    assert result is not None
    assert result["price"] == 27.77
    assert result["source"] == "江西信息价(2025-08-31)"


def test_search_price_by_name_does_not_fallback_to_other_province_official_price(tmp_path: Path):
    db = MaterialDB(str(tmp_path / "material.db"))

    material_id = db.add_material("衬塑钢管", "DN100", "m")
    db.add_price(
        material_id,
        85.88,
        "official_info",
        province="陕西",
        city="西安",
        unit="m",
        period_end="2025-08-31",
    )
    db.add_price(
        material_id,
        58.00,
        "market_web",
        province="江西",
        city="九江",
        unit="m",
        period_end="2025-08-31",
    )

    result_all = db.search_price_by_name(
        "衬塑钢管",
        province="江西",
        city="九江",
        period_end="2025-08-31",
        spec="DN100",
        target_unit="m",
    )
    result_info = db.search_price_by_name(
        "衬塑钢管",
        province="江西",
        city="九江",
        period_end="2025-08-31",
        spec="DN100",
        target_unit="m",
        source_type="government",
    )

    assert result_all is not None
    assert result_all["price"] == 58.00
    assert "市场价" in result_all["source"]
    assert result_info is None


def test_search_price_by_name_includes_user_contributed_market_price(tmp_path: Path):
    db = MaterialDB(str(tmp_path / "material.db"))

    material_id = db.add_material("球墨铸铁井盖", "700*800", "套")
    db.add_price(
        material_id,
        420.0,
        "user_contribute",
        province="广东",
        city="广州",
        unit="套",
        period_end="2026-04-01",
    )

    result = db.search_price_by_name(
        "球墨铸铁井盖",
        province="广东",
        city="广州",
        spec="700*800",
        target_unit="套",
    )

    assert result is not None
    assert result["price"] == 420.0
    assert "市场价" in result["source"]
