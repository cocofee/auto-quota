from src.bill_cleaner import _is_non_matchable_title_item, clean_bill_items


def test_non_matchable_title_item_filters_summary_sheet_project_row():
    item = {
        "name": "4-2单元-给排水工程",
        "description": "",
        "sheet_name": "表03单项工程投标价汇总表",
        "unit": "245560",
        "quantity": 78058.99,
        "code": "1",
    }

    assert _is_non_matchable_title_item(item) is True


def test_clean_bill_items_filters_summary_titles_and_keeps_real_bill_rows():
    items = [
        {
            "name": "询价材料",
            "description": "",
            "sheet_name": "表04单位工程投标报价汇总表",
            "unit": "",
            "quantity": "",
            "code": "",
        },
        {
            "name": "复合管",
            "description": "DN25",
            "section": "给水系统",
            "sheet_name": "表08A分部分项工程量清单与计价表",
            "unit": "m",
            "quantity": 1,
            "code": "030801001",
        },
    ]

    cleaned = clean_bill_items(items, province="安徽省安装工程计价定额(2018)")

    assert [item["name"] for item in cleaned] == ["复合管"]
