from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.append(str(TOOLS_DIR))


def _load_local_match_server(monkeypatch):
    monkeypatch.setenv("LOCAL_MATCH_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "main", types.ModuleType("main"))
    if "local_match_server" in sys.modules:
        return importlib.reload(sys.modules["local_match_server"])
    return importlib.import_module("local_match_server")


def test_search_material_web_extracts_detail_url():
    gldjc_price = importlib.import_module("gldjc_price")

    class _FakeResponse:
        text = """
        <div class="search-card">
          <a href="/info/123.html">
            <div class="m-detail-content">HDPE给水管 De63</div>
            <div class="brand-box">品牌A<div></div></div>
            <div class="colspan-cell width-56 pure-text">m</div>
            <div class="price-block"><span class="change-point">128.82</span></div>
          </a>
        </div>
        """

        def raise_for_status(self):
            return None

    class _FakeSession:
        def get(self, *_args, **_kwargs):
            return _FakeResponse()

    results = gldjc_price.search_material_web(_FakeSession(), "HDPE给水管 De63", "440000")

    assert results[0]["detail_url"] == "https://www.gldjc.com/info/123.html"


def test_remote_lookup_does_not_use_gldjc_market_cache(monkeypatch):
    server = _load_local_match_server(monkeypatch)

    class _FakeDB:
        def search_price_by_name(self, *_args, **_kwargs):
            return None

    import src.material_db as material_db

    monkeypatch.setattr(material_db, "MaterialDB", lambda: _FakeDB())
    monkeypatch.setattr(
        server,
        "_load_material_price_cache",
        lambda: {
            "泄水阀|DE63|个|广东": {
                "price_with_tax": 128.82,
                "searched_keyword": "泄水阀 De63",
                "gldjc_url": "https://www.gldjc.com/scj/so.html?keyword=%E6%B3%84%E6%B0%B4%E9%98%80%20De63&l=440000",
                "match_label": "品牌A | 泄水阀 De63 | 个 | 128.82",
                "matched_spec": "De63",
                "matched_unit": "个",
            },
            "泄水阀|DE63|个|全国": {
                "price_with_tax": 3489.00,
                "searched_keyword": "泄水阀 De63",
                "gldjc_url": "https://www.gldjc.com/scj/so.html?keyword=%E6%B3%84%E6%B0%B4%E9%98%80%20De63&l=1",
                "match_label": "品牌B | 泄水阀 De63 | 个 | 3489.00",
                "matched_spec": "De63",
                "matched_unit": "个",
            },
        },
    )

    result = server.material_price_lookup(
        {
            "materials": [{"name": "泄水阀", "spec": "De63", "unit": "个"}],
            "province": "广东",
            "price_type": "all",
        },
        x_api_key=server.API_KEY,
    )

    row = result["results"][0]
    assert row["lookup_price"] is None
    assert row["lookup_source"] == "未查到"
    assert row["lookup_label"] is None
    assert row["lookup_url"] is None


def test_remote_gldjc_lookup_uses_selected_province_area_code(monkeypatch):
    server = _load_local_match_server(monkeypatch)
    gldjc_price = importlib.import_module("gldjc_price")

    calls: list[tuple[str, str]] = []

    class _CookieJar:
        def set(self, *_args, **_kwargs):
            return None

    class _FakeSession:
        def __init__(self):
            self.cookies = _CookieJar()

    monkeypatch.setattr(requests, "Session", _FakeSession)
    monkeypatch.setattr(
        gldjc_price,
        "parse_material",
        lambda name, spec: {"base_name": name, "specs": [spec] if spec else [], "search_keywords": [f"{name} {spec}".strip()]},
    )
    monkeypatch.setattr(
        gldjc_price,
        "build_region_search_plans",
        lambda keywords, province="", city="": [
            {"keyword": keywords[0], "area_code": "440000", "scope": "广东"},
            {"keyword": keywords[0], "area_code": "1", "scope": "全国"},
        ],
    )

    def _fake_search(_session, keyword, area_code="1"):
        calls.append((keyword, area_code))
        if area_code == "440000":
            return [{
                "spec": "HDPE给水管 De63",
                "brand": "品牌A",
                "unit": "m",
                "market_price": 128.82,
                "score": 95,
                "detail_url": "https://www.gldjc.com/info/hdpe-de63-1.html",
            }]
        return [{"spec": "HDPE给水管 De63", "brand": "品牌B", "unit": "m", "market_price": 3489.00, "score": 10}]

    monkeypatch.setattr(gldjc_price, "search_material_web", _fake_search)
    monkeypatch.setattr(gldjc_price, "filter_and_score", lambda results, *_args, **_kwargs: results)
    monkeypatch.setattr(gldjc_price, "determine_confidence", lambda *_args, **_kwargs: "高")
    monkeypatch.setattr(gldjc_price, "get_representative_price_result", lambda results: results[0] if results else None)
    monkeypatch.setattr(gldjc_price, "build_match_label", lambda result, fallback_text="查看": f"{result['brand']} | {result['spec']} | {result['unit']} | {result['market_price']:.2f}" if result else fallback_text)
    monkeypatch.setattr(gldjc_price, "load_cache", lambda: {})
    monkeypatch.setattr(gldjc_price, "save_cache", lambda _cache: None)
    monkeypatch.setattr(gldjc_price, "update_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(gldjc_price, "check_cache", lambda *args, **kwargs: None)

    req = server._GldjcLookupRequest(
        materials=[{"name": "HDPE给水管", "spec": "De63", "unit": "m"}],
        cookie="token=test",
        province="广东",
        city="广州",
    )
    result = server.material_price_gldjc_lookup(req, x_api_key=server.API_KEY)

    row = result["results"][0]
    assert calls == [("HDPE给水管 De63", "440000")]
    assert row["gldjc_price"] == 128.82
    assert row["gldjc_url"] == "https://www.gldjc.com/info/hdpe-de63-1.html"


def test_remote_gldjc_lookup_converts_ton_price_to_meter(monkeypatch):
    server = _load_local_match_server(monkeypatch)
    gldjc_price = importlib.import_module("gldjc_price")

    class _CookieJar:
        def set(self, *_args, **_kwargs):
            return None

    class _FakeSession:
        def __init__(self):
            self.cookies = _CookieJar()

    monkeypatch.setattr(requests, "Session", _FakeSession)
    monkeypatch.setattr(
        gldjc_price,
        "parse_material",
        lambda name, spec: {"base_name": name, "specs": [spec] if spec else [], "search_keywords": [f"{name} {spec}".strip()]},
    )
    monkeypatch.setattr(
        gldjc_price,
        "build_region_search_plans",
        lambda keywords, province="", city="": [
            {"keyword": keywords[0], "area_code": "360000", "scope": "江西"},
        ],
    )
    monkeypatch.setattr(
        gldjc_price,
        "search_material_web",
        lambda _session, _keyword, _area_code="1": [
            {"spec": "镀锌焊接钢管 DN25", "brand": "利达", "unit": "t", "market_price": 3663.72},
        ],
    )
    monkeypatch.setattr(gldjc_price, "load_cache", lambda: {})
    monkeypatch.setattr(gldjc_price, "save_cache", lambda _cache: None)
    monkeypatch.setattr(gldjc_price, "update_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(gldjc_price, "check_cache", lambda *args, **kwargs: None)

    req = server._GldjcLookupRequest(
        materials=[{"name": "镀锌钢管", "spec": "DN25", "unit": "m"}],
        cookie="token=test",
        province="江西",
        city="九江",
    )
    result = server.material_price_gldjc_lookup(req, x_api_key=server.API_KEY)

    row = result["results"][0]
    assert row["gldjc_price"] == 9.36
    assert "由t换算" in row["gldjc_label"]


def test_remote_gldjc_lookup_falls_back_to_approximate_price(monkeypatch):
    server = _load_local_match_server(monkeypatch)
    gldjc_price = importlib.import_module("gldjc_price")

    class _CookieJar:
        def set(self, *_args, **_kwargs):
            return None

    class _FakeSession:
        def __init__(self):
            self.cookies = _CookieJar()

    monkeypatch.setattr(requests, "Session", _FakeSession)
    monkeypatch.setattr(
        gldjc_price,
        "parse_material",
        lambda name, spec: {"base_name": name, "specs": [spec] if spec else [], "search_keywords": [f"{name} {spec}".strip()]},
    )
    monkeypatch.setattr(
        gldjc_price,
        "build_region_search_plans",
        lambda keywords, province="", city="": [
            {"keyword": keywords[0], "area_code": "360000", "scope": "江西"},
        ],
    )
    monkeypatch.setattr(
        gldjc_price,
        "search_material_web",
        lambda _session, _keyword, _area_code="1": [
            {"spec": "品种 : 镀锌焊接钢管 | 公称直径DN(mm) : 100 | 牌号 : 20", "brand": "利达", "unit": "t", "market_price": 3424.78},
        ],
    )
    monkeypatch.setattr(gldjc_price, "load_cache", lambda: {})
    monkeypatch.setattr(gldjc_price, "save_cache", lambda _cache: None)
    monkeypatch.setattr(gldjc_price, "update_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(gldjc_price, "check_cache", lambda *args, **kwargs: None)

    req = server._GldjcLookupRequest(
        materials=[{"name": "衬塑钢管", "spec": "DN100", "unit": "m"}],
        cookie="token=test",
        province="江西",
        city="九江",
    )
    result = server.material_price_gldjc_lookup(req, x_api_key=server.API_KEY)

    row = result["results"][0]
    assert row["gldjc_price"] == 41.61
    assert row["gldjc_source"] == "广材网近似价(江西)"
    assert row["gldjc_url"] is not None
    assert row["gldjc_label"].startswith("近似价 | ")


def test_remote_lookup_passes_city_and_period_to_db(monkeypatch):
    server = _load_local_match_server(monkeypatch)

    class _FakeDB:
        def search_price_by_name(self, name: str, **kwargs):
            assert name == "焊接钢管"
            assert kwargs["province"] == "江西"
            assert kwargs["city"] == "九江"
            assert kwargs["period_end"] == "2025-08-31"
            assert kwargs["spec"] == "DN80"
            assert kwargs["target_unit"] == "m"
            return {
                "price": 28.04,
                "unit": "m",
                "source": "江西九江信息价(2025-08-31)",
            }

    import src.material_db as material_db

    monkeypatch.setattr(material_db, "MaterialDB", lambda: _FakeDB())
    monkeypatch.setattr(server, "_load_material_price_cache", lambda: {})

    result = server.material_price_lookup(
        {
            "materials": [{"name": "焊接钢管", "spec": "DN80", "unit": "m"}],
            "province": "江西",
            "city": "九江",
            "period_end": "2025-08-31",
            "price_type": "info",
        },
        x_api_key=server.API_KEY,
    )

    row = result["results"][0]
    assert row["lookup_price"] == 28.04
    assert row["lookup_source"] == "江西九江信息价(2025-08-31)"


def test_gldjc_cookie_verify_reports_valid(monkeypatch):
    server = _load_local_match_server(monkeypatch)
    gldjc_price = importlib.import_module("gldjc_price")

    class _FakeResponse:
        text = '<div class="price-block"><span class="change-point">123.45</span></div>'

        def raise_for_status(self):
            return None

    class _FakeSession:
        def __init__(self):
            self.cookies = requests.cookies.RequestsCookieJar()

        def get(self, *_args, **_kwargs):
            return _FakeResponse()

    monkeypatch.setattr(requests, "Session", _FakeSession)
    monkeypatch.setattr(gldjc_price, "resolve_gldjc_area_code", lambda province="", city="": "360000" if province else "1")
    monkeypatch.setattr(gldjc_price, "_get_headers", lambda: {})

    result = server.material_price_gldjc_cookie_verify(
        server._GldjcCookieVerifyRequest(cookie="token=bearer test", province="江西"),
        x_api_key=server.API_KEY,
    )

    assert result["ok"] is True
    assert result["status"] == "valid"
    assert result["scope"] == "江西"
    assert result["area_code"] == "360000"


def test_gldjc_cookie_verify_reports_invalid(monkeypatch):
    server = _load_local_match_server(monkeypatch)
    gldjc_price = importlib.import_module("gldjc_price")

    class _FakeResponse:
        text = "请登录后查看"

        def raise_for_status(self):
            return None

    class _FakeSession:
        def __init__(self):
            self.cookies = requests.cookies.RequestsCookieJar()

        def get(self, *_args, **_kwargs):
            return _FakeResponse()

    monkeypatch.setattr(requests, "Session", _FakeSession)
    monkeypatch.setattr(gldjc_price, "resolve_gldjc_area_code", lambda province="", city="": "1")
    monkeypatch.setattr(gldjc_price, "_get_headers", lambda: {})

    result = server.material_price_gldjc_cookie_verify(
        server._GldjcCookieVerifyRequest(cookie="token=bearer test"),
        x_api_key=server.API_KEY,
    )

    assert result["ok"] is False
    assert result["status"] == "invalid"
