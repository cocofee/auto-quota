from src.specialty_classifier import classify


def test_section_and_family_build_strict_book_constraints():
    result = classify(
        "普通UPVC排水管",
        "室内安装 污水 DN50 粘接",
        section_title="重力排水系统",
        context_prior={"system_hint": "给排水"},
        canonical_features={"family": "valve_body"},
    )

    assert result["primary"] == "C10"
    assert result["route_mode"] == "strict"
    assert result["allow_cross_book_escape"] is False
    assert result["search_books"][0] == "C10"
    assert "C10" in result["hard_book_constraints"]


def test_sheet_name_and_description_can_anchor_book_without_section():
    result = classify(
        "阀门",
        "系统:污水 管径:DN100 连接:法兰",
        sheet_name="08A分部分项工程量清单与计价表-重力排水系统",
        context_prior={
            "batch_context": {
                "sheet_system_hint": "给排水",
            }
        },
        canonical_features={"family": "valve_body"},
    )

    assert result["primary"] == "C10"
    assert result["route_mode"] == "strict"
    assert result["allow_cross_book_escape"] is False
    assert result["search_books"][0] == "C10"
    assert "C10" in result["hard_book_constraints"]
    assert any(reason.startswith("sheet:") for reason in result["routing_evidence"]["C10"])
    assert any("desc_system_hint" in reason for reason in result["routing_evidence"]["C10"])


def test_insulation_keywords_can_anchor_c12_without_section():
    result = classify(
        "防结露保温",
        "绝热材料品种：难燃性闭孔橡塑泡沫 管道外径：DN80以下",
    )

    assert result["primary"] == "C12"
    assert result["route_mode"] == "strict"
    assert result["allow_cross_book_escape"] is False
    assert result["search_books"][0] == "C12"
    assert "C12" in result["hard_book_constraints"]
