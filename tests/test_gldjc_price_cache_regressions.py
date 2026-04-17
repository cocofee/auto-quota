from __future__ import annotations

import json
from pathlib import Path


def test_material_price_cache_does_not_keep_known_anhui_mismatches():
    cache_path = Path(__file__).resolve().parents[1] / "data" / "material_prices.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))

    bad_keys = {
        "室外塑料给水管热熔管件|DE32|个|安徽",
        "室外塑料给水管热熔管件|DE63|个|安徽",
        "室外塑料给水管热熔管件|DE110|个|安徽",
        "绿地灌溉管线安装|DE16|m|安徽",
        "室外塑料给水管热熔管件|DE16|个|安徽",
    }

    assert bad_keys.isdisjoint(cache)
