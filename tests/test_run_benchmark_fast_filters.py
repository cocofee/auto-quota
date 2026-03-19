from tools.run_benchmark import filter_json_papers


def test_filter_json_papers_keeps_only_install_provinces():
    papers = {
        "福建省通用安装工程预算定额(2017)": {"items": [{"bill_name": "配电箱", "bill_text": "", "quota_names": []}]},
        "福建省房屋建筑与装饰工程预算定额(2017)": {"items": [{"bill_name": "金属窗", "bill_text": "", "quota_names": []}]},
    }

    filtered = filter_json_papers(papers, install_only=True)

    assert list(filtered) == ["福建省通用安装工程预算定额(2017)"]


def test_filter_json_papers_matches_item_keywords_against_bill_and_answer():
    papers = {
        "福建省通用安装工程预算定额(2017)": {
            "items": [
                {"bill_name": "配电箱", "bill_text": "名称:照明配电箱", "quota_names": ["成套配电箱安装 4AL"]},
                {"bill_name": "配管", "bill_text": "JDG20 暗敷", "quota_names": ["紧定式薄壁钢管敷设"]},
                {"bill_name": "控制柜基础", "bill_text": "", "quota_names": ["高压成套配电柜安装 断路器柜"]},
            ]
        }
    }

    filtered = filter_json_papers(papers, item_keywords=["配电箱", "配电柜"])
    items = filtered["福建省通用安装工程预算定额(2017)"]["items"]

    assert len(items) == 2
    assert items[0]["bill_name"] == "配电箱"
    assert items[1]["bill_name"] == "控制柜基础"


def test_filter_json_papers_applies_per_province_limit_after_keyword_filter():
    papers = {
        "福建省通用安装工程预算定额(2017)": {
            "items": [
                {"bill_name": "配电箱A", "bill_text": "配电箱", "quota_names": []},
                {"bill_name": "配电箱B", "bill_text": "配电箱", "quota_names": []},
                {"bill_name": "配电箱C", "bill_text": "配电箱", "quota_names": []},
            ]
        }
    }

    filtered = filter_json_papers(
        papers,
        item_keywords=["配电箱"],
        max_items_per_province=2,
    )

    items = filtered["福建省通用安装工程预算定额(2017)"]["items"]
    assert [item["bill_name"] for item in items] == ["配电箱A", "配电箱B"]


def test_filter_json_papers_install_only_keeps_install_items_in_mixed_province():
    papers = {
        "北京市建设工程施工消耗量标准(2024)": {
            "items": [
                {
                    "bill_name": "配电箱",
                    "bill_text": "名称:配电箱 明装",
                    "specialty": "C4",
                    "quota_ids": ["C4-4-31"],
                    "quota_names": ["配电箱墙上明装"],
                },
                {
                    "bill_name": "混凝土垫层",
                    "bill_text": "100厚C15混凝土",
                    "specialty": "A",
                    "quota_ids": ["1-1-1"],
                    "quota_names": ["混凝土垫层"],
                },
            ]
        }
    }

    filtered = filter_json_papers(papers, install_only=True)

    items = filtered["北京市建设工程施工消耗量标准(2024)"]["items"]
    assert [item["bill_name"] for item in items] == ["配电箱"]


def test_filter_json_papers_install_only_drops_non_install_province_even_with_c_like_specialty():
    papers = {
        "宁夏房屋建筑装饰工程计价定额(2019)": {
            "items": [
                {
                    "bill_name": "瓷砖地面",
                    "bill_text": "800*800mm瓷砖地面",
                    "specialty": "C1",
                    "quota_ids": ["1-11-51"],
                    "quota_names": ["块料面层 陶瓷地面砖"],
                }
            ]
        }
    }

    filtered = filter_json_papers(papers, install_only=True)

    assert filtered == {}
