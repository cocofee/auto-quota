from src.review_checkers import check_connection_mismatch


def test_check_connection_mismatch_skips_water_meter_items():
    item = {
        "name": "水表",
        "description": "规格类型:DN50 丝扣连接",
    }
    error = check_connection_mismatch(
        item,
        "法兰式水表组成安装 公称直径(mm以内) 50",
        ["规格类型:DN50 丝扣连接"],
    )

    assert error is None


def test_check_connection_mismatch_skips_vacuum_breaker_items():
    item = {
        "name": "倒流防止器",
        "description": "名称、类型:真空破坏器 规格类型:DN20 丝扣连接",
    }
    error = check_connection_mismatch(
        item,
        "真空破坏器安装(法兰连接) 公称直径(mm以内) 20",
        ["名称、类型:真空破坏器", "规格类型:DN20 丝扣连接"],
    )

    assert error is None
