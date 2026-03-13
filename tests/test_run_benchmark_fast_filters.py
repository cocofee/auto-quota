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
