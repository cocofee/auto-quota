from src.specialty_classifier import classify


def test_bill_code_yields_to_strong_give_water_text_signal():
    result = classify(
        "给水管道安装",
        "",
        bill_code="030101002001",
    )

    assert result["primary"] == "C10"
    assert "文本信号覆盖编码" in result["reason"]


def test_bill_code_yields_to_consistent_drainage_text_signal():
    result = classify(
        "排水管道安装",
        "De110 粘接 UPVC管",
        bill_code="030101003001",
    )

    assert result["primary"] == "C10"
    assert "文本信号覆盖编码" in result["reason"]


def test_bill_code_keeps_mechanical_when_text_signal_is_not_strong_enough():
    result = classify(
        "泵安装",
        "",
        bill_code="030101001001",
    )

    assert result["primary"] == "C1"
    assert "清单编码匹配" in result["reason"]
