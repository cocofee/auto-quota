from pathlib import Path
import sys


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.append(str(TOOLS_DIR))

from gldjc_price import (  # noqa: E402
    _extract_material_tokens,
    _parse_tax_rate_multiplier,
    build_region_search_plans,
    check_cache,
    determine_confidence,
    filter_and_score,
    GldjcCookieInvalidError,
    get_cache_key,
    is_non_material,
    parse_material,
    get_representative_price_result,
    resolve_gldjc_area_code,
    search_material_web,
    update_cache,
)


def test_filter_and_score_converts_ton_price_to_meter_for_steel_pipe():
    results = [
        {"spec": "镀锌焊接钢管 DN25", "unit": "t", "market_price": 3663.72},
    ]

    scored = filter_and_score(results, "m", ["DN25"], "镀锌钢管", request_name="镀锌钢管", request_spec="DN25")

    assert len(scored) == 1
    assert scored[0]["unit"] == "m"
    assert scored[0]["market_price"] == 9.36
    assert scored[0]["_converted_from_unit"] == "t"


def test_parse_material_adds_valve_fallback_keywords_for_copper_stop_valve():
    parsed = parse_material("铜截止阀 DN40")

    assert "铜截止阀 DN40" in parsed["search_keywords"]
    assert "截止阀 DN40" in parsed["search_keywords"]
    assert "铜阀门 DN40" in parsed["search_keywords"]
    assert "阀门 DN40" in parsed["search_keywords"]


def test_extract_material_tokens_keeps_valve_subtype():
    tokens = _extract_material_tokens("铜截止阀")

    assert "截止阀" in tokens
    assert "铜" in tokens


def test_is_non_material_does_not_drop_real_material_rows_with_process_keywords():
    assert is_non_material("现场制作镀锌风管") is False
    assert is_non_material("试压管件") is False
    assert is_non_material("冲洗套管") is False
    assert is_non_material("临时设施费") is True


def test_parse_tax_rate_multiplier_accepts_percent_string_and_numeric_forms():
    assert _parse_tax_rate_multiplier("13%") == 1.13
    assert _parse_tax_rate_multiplier("13") == 1.13
    assert _parse_tax_rate_multiplier(13) == 1.13
    assert _parse_tax_rate_multiplier(0.13) == 1.13
    assert _parse_tax_rate_multiplier("") is None


def test_resolve_gldjc_area_code_accepts_name_and_numeric_code():
    assert resolve_gldjc_area_code("湖北") == "420000"
    assert resolve_gldjc_area_code("420000") == "420000"
    assert resolve_gldjc_area_code("1") == "1"


def test_filter_and_score_rejects_composite_pipe_matching_galvanized_pipe():
    results = [
        {"spec": "品种 : 镀锌焊接钢管 | 公称直径DN(mm) : 100 | 牌号 : 20", "unit": "t", "market_price": 3424.78},
    ]

    scored = filter_and_score(results, "m", ["DN100"], "衬塑钢管", request_name="衬塑钢管", request_spec="DN100")

    assert scored == []


def test_filter_and_score_requires_target_spec_match():
    results = [
        {"spec": "镀锌钢管 DN32", "unit": "m", "market_price": 18.5},
    ]

    scored = filter_and_score(results, "m", ["DN25"], "镀锌钢管")

    assert scored == []


def test_determine_confidence_stays_low_when_target_unit_missing_in_result():
    scored_results = [
        {"score": 80, "_unit_match": False, "_spec_match": True},
    ]

    confidence = determine_confidence(scored_results, "m", ["DN25"])

    assert confidence == "低"


def test_filter_and_score_rejects_pipe_fitting_result_for_pipe_target():
    results = [
        {"spec": "HDPE热熔管件 De63", "unit": "m", "market_price": 432.83},
    ]

    scored = filter_and_score(results, "m", ["De63"], "给水管HDPE")

    assert scored == []


def test_filter_and_score_rejects_pipe_result_for_fitting_target():
    results = [
        {"spec": "HDPE给水管 De110", "unit": "个", "market_price": 3479.77},
    ]

    scored = filter_and_score(results, "个", ["De110"], "室外塑料给水管热熔管件")

    assert scored == []


def test_filter_and_score_rejects_pipe_fitting_accessory_for_heat_fusion_target():
    results = [
        {"spec": "法兰管卡 De110", "unit": "个", "market_price": 3479.77},
        {"spec": "U型管卡 De63", "unit": "个", "market_price": 17.13},
    ]

    scored = filter_and_score(results, "个", ["De110"], "室外塑料给水管热熔管件")

    assert scored == []


def test_filter_and_score_accepts_dn_mm_style_spec_for_meter():
    results = [
        {"spec": "普通旋翼式水表 表盘DN(mm):50", "unit": "个", "market_price": 339.99},
    ]

    scored = filter_and_score(results, "个", ["DN50"], "水表")

    assert len(scored) == 1
    assert scored[0]["market_price"] == 339.99


def test_filter_and_score_relaxed_spec_keeps_same_family_and_unit_results():
    results = [
        {"spec": "PP-R给水管 S5", "unit": "m", "market_price": 23.5},
    ]

    scored = filter_and_score(results, "m", ["DN63"], "PP-R管", allow_relaxed_spec=True)

    assert len(scored) == 1
    assert scored[0]["market_price"] == 23.5
    assert scored[0].get("_relaxed_spec_match") is True


def test_cache_key_separates_same_name_by_spec():
    cache = {}

    update_cache(cache, "PE给水管", "m", {"price_with_tax": 12.3, "query_date": "2099-01-01"}, spec="De32")
    update_cache(cache, "PE给水管", "m", {"price_with_tax": 45.6, "query_date": "2099-01-01"}, spec="De63")

    assert get_cache_key("PE给水管", "m", "De32") != get_cache_key("PE给水管", "m", "De63")
    assert check_cache(cache, "PE给水管", "m", "De32")["price_with_tax"] == 12.3
    assert check_cache(cache, "PE给水管", "m", "De63")["price_with_tax"] == 45.6


def test_cache_key_separates_same_name_by_region():
    cache = {}

    update_cache(cache, "泄水阀", "个", {"price_with_tax": 128.82, "query_date": "2099-01-01"}, spec="De63", region="广东")
    update_cache(cache, "泄水阀", "个", {"price_with_tax": 140.66, "query_date": "2099-01-01"}, spec="De63", region="全国")

    assert get_cache_key("泄水阀", "个", "De63", "广东") != get_cache_key("泄水阀", "个", "De63", "全国")
    assert check_cache(cache, "泄水阀", "个", "De63", region="广东")["price_with_tax"] == 128.82
    assert check_cache(cache, "泄水阀", "个", "De63", region="全国")["price_with_tax"] == 140.66


def test_representative_price_result_avoids_extreme_outlier():
    prices = [
        {"market_price": 3489.0, "score": 95, "spec": "泄水阀 De63", "unit": "个"},
        {"market_price": 128.82, "score": 90, "spec": "泄水阀 De63", "unit": "个"},
        {"market_price": 129.50, "score": 88, "spec": "泄水阀 De63", "unit": "个"},
    ]

    result = get_representative_price_result(prices)

    assert result is not None
    assert result["market_price"] in {128.82, 129.50}
    assert result["market_price"] < 1000


def test_representative_price_result_prefers_direct_unit_cluster_over_converted_prices():
    prices = [
        {"market_price": 105.42, "score": 95, "spec": "衬塑钢管 DN100", "unit": "m", "_converted_from_unit": "t"},
        {"market_price": 121.28, "score": 94, "spec": "衬塑钢管 DN100", "unit": "m", "_converted_from_unit": "t"},
        {"market_price": 169.74, "score": 93, "spec": "衬塑钢管 DN100", "unit": "m"},
        {"market_price": 75.67, "score": 92, "spec": "衬塑钢管 DN100", "unit": "m"},
        {"market_price": 81.83, "score": 92, "spec": "衬塑钢管 DN100", "unit": "m"},
    ]

    result = get_representative_price_result(prices)

    assert result is not None
    assert result["market_price"] == 75.67
    assert "_converted_from_unit" not in result


def test_search_material_web_skips_no_price_cards_without_result_misalignment():
    html = """
    <div class="result-list">
      <div class="item-card">
        <div class="brand-box">无价品牌<div></div></div>
        <div class="m-detail-content">水表 DN50</div>
        <div class="colspan-cell width-56 pure-text">块</div>
      </div>
      <div class="item-card">
        <div class="brand-box">可拆式水表<div></div></div>
        <div class="m-detail-content">水表 DN50</div>
        <div class="colspan-cell width-56 pure-text">个</div>
        <div class="price-block"><span class="change-point">339.99</span></div>
      </div>
    </div>
    """

    class _FakeResponse:
        text = html

        def raise_for_status(self):
            return None

    class _FakeSession:
        def get(self, *_args, **_kwargs):
            return _FakeResponse()

    results = search_material_web(_FakeSession(), "水表 DN50")

    assert results == [
        {
            "keyword": "水表 DN50",
            "spec": "水表 DN50",
            "brand": "可拆式水表",
            "unit": "个",
            "market_price": 339.99,
            "detail_url": "",
        }
    ]


def test_search_material_web_raises_clear_error_when_cookie_invalid():
    class _FakeResponse:
        text = "<html><body>请登录</body></html>"

        def raise_for_status(self):
            return None

    class _FakeSession:
        def get(self, *_args, **_kwargs):
            return _FakeResponse()

    try:
        search_material_web(_FakeSession(), "水表 DN50")
    except GldjcCookieInvalidError as exc:
        assert "Cookie" in str(exc)
    else:
        raise AssertionError("expected GldjcCookieInvalidError")


def test_build_region_search_plans_prefers_province_then_national():
    plans = build_region_search_plans(["泄水阀 De63", "泄水阀"], province="广东", city="广州")

    assert plans == [
        {"keyword": "泄水阀 De63", "area_code": "440000", "scope": "广东"},
        {"keyword": "泄水阀", "area_code": "440000", "scope": "广东"},
        {"keyword": "泄水阀 De63", "area_code": "1", "scope": "全国"},
        {"keyword": "泄水阀", "area_code": "1", "scope": "全国"},
    ]
