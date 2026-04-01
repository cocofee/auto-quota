from tools.extract_top_gaps import analyze_parser_gaps, build_top_gap_report


def _sample_records():
    return [
        {
            "province": "测试省",
            "bill_name": "配管",
            "bill_text": "配管 材质：JDG 规格：20 配置形式:暗敷",
            "expected_quota_names": ["镀锌电线管砖、混凝土结构暗配 公称直径(mm以内) 20"],
            "is_match": False,
            "oracle_in_candidates": True,
        },
        {
            "province": "测试省",
            "bill_name": "双联单控",
            "bill_text": "双联单控 安装方式:墙面暗装",
            "expected_quota_names": ["普通开关、按钮安装 跷板暗开关 单控≤3联"],
            "is_match": False,
            "oracle_in_candidates": True,
        },
        {
            "province": "测试省",
            "bill_name": "KL",
            "bill_text": "KL 控制电缆",
            "expected_quota_names": ["控制电缆敷设"],
            "cause": "synonym_gap",
            "is_match": False,
            "oracle_in_candidates": False,
            "miss_category": "recall_miss",
        },
        {
            "province": "测试省",
            "bill_name": "配电箱",
            "bill_text": "配电箱 成套 安装 调试",
            "expected_quota_names": ["配电箱安装"],
            "cause": "search_word_miss",
            "is_match": False,
            "oracle_in_candidates": False,
            "miss_category": "recall_miss",
        },
    ]


def test_analyze_parser_gaps_detects_missing_primary_params():
    report = analyze_parser_gaps(_sample_records(), top_n=10)

    assert report["parser_gap_case_count"] == 0
    assert report["top_parser_missing_params"] == []


def test_build_top_gap_report_combines_parser_and_recall_sections():
    report = build_top_gap_report(
        _sample_records(),
        top_parser=10,
        top_synonyms=10,
        top_terms=10,
        top_rules=10,
    )

    assert report["parser_gaps"]["parser_gap_case_count"] == 0
    assert report["recall_gaps"]["recall_miss_total"] == 2
    assert report["recall_gaps"]["top_missing_synonyms"][0]["key"] == "KL -> 控制电缆敷设"


def test_analyze_parser_gaps_ignores_unmentioned_half_perimeter_case():
    records = [
        {
            "province": "测试省",
            "bill_name": "配电箱",
            "bill_text": "配电箱 1#AL1",
            "expected_quota_names": ["成套配电箱安装 悬挂式(半周长m以内) 1.5"],
            "is_match": False,
            "oracle_in_candidates": True,
        }
    ]

    report = analyze_parser_gaps(records, top_n=10)

    assert report["parser_gap_case_count"] == 0
    assert report["top_parser_missing_params"] == []


def test_analyze_parser_gaps_ignores_non_electrical_cable_section_sizes():
    records = [
        {
            "province": "测试省",
            "bill_name": "软膜天花",
            "bill_text": "软膜天花 龙骨材料种类、规格、中距:轻钢龙骨 面层材料品种、规格:铝合金边框加厚软膜天花",
            "expected_quota_names": ["吊顶天棚 规格(mm) 300×300 平面"],
            "is_match": False,
            "oracle_in_candidates": True,
        }
    ]

    report = analyze_parser_gaps(records, top_n=10)

    assert report["parser_gap_case_count"] == 0
    assert report["top_parser_missing_params"] == []


def test_analyze_parser_gaps_ignores_non_hvac_perimeter_sizes():
    records = [
        {
            "province": "测试省",
            "bill_name": "块料楼地面",
            "bill_text": "块料楼地面 10mm厚浅色防滑地砖600×600mm",
            "expected_quota_names": ["(地砖楼地面 水泥砂浆结合层 不勾缝 周长3200mm以内)"],
            "is_match": False,
            "oracle_in_candidates": True,
        }
    ]

    report = analyze_parser_gaps(records, top_n=10)

    assert report["parser_gap_case_count"] == 0
    assert report["top_parser_missing_params"] == []


def test_analyze_parser_gaps_keeps_hvac_perimeter_cases():
    records = [
        {
            "province": "测试省",
            "bill_name": "电动百叶风口",
            "bill_text": "电动百叶风口 规格：400*(2000+250)",
            "expected_quota_names": ["百叶风口安装 周长(mm以内) 6000"],
            "is_match": False,
            "oracle_in_candidates": True,
        }
    ]

    report = analyze_parser_gaps(records, top_n=10)

    assert report["parser_gap_case_count"] == 1
    assert report["top_parser_missing_params"][0]["key"] == "perimeter"


def test_analyze_parser_gaps_ignores_non_pipe_diameter_cases():
    records = [
        {
            "province": "测试省",
            "bill_name": "普通灯具",
            "bill_text": "普通灯具 名称:LED感应吸顶灯 规格:12W 4000K 1200lm 安装方式:吸顶",
            "expected_quota_names": ["吸顶灯具安装 灯罩直径(mm以内)250"],
            "is_match": False,
            "oracle_in_candidates": True,
        }
    ]

    report = analyze_parser_gaps(records, top_n=10)

    assert report["parser_gap_case_count"] == 0
    assert report["top_parser_missing_params"] == []


def test_analyze_parser_gaps_requires_explicit_item_length_cues():
    records = [
        {
            "province": "测试省",
            "bill_name": "墙面脚手架",
            "bill_text": "墙面脚手架 22m",
            "expected_quota_names": ["钢管脚手架(双排5m以内)"],
            "is_match": False,
            "oracle_in_candidates": True,
        }
    ]

    report = analyze_parser_gaps(records, top_n=10)

    assert report["parser_gap_case_count"] == 0
    assert report["top_parser_missing_params"] == []


def test_analyze_parser_gaps_ignores_generic_cable_without_section_value():
    records = [
        {
            "province": "测试省",
            "bill_name": "电缆",
            "bill_text": "电缆",
            "expected_quota_names": ["铜芯电力电缆敷设ZC-YJV-4*4+1*2.5"],
            "is_match": False,
            "oracle_in_candidates": True,
        }
    ]

    report = analyze_parser_gaps(records, top_n=10)

    assert report["parser_gap_case_count"] == 0
    assert report["top_parser_missing_params"] == []


def test_analyze_parser_gaps_ignores_generic_silencer_without_perimeter_value():
    records = [
        {
            "province": "测试省",
            "bill_name": "消声器",
            "bill_text": "消声器",
            "expected_quota_names": ["阻抗复合式消声器安装 周长(mm以内) 2400"],
            "is_match": False,
            "oracle_in_candidates": True,
        }
    ]

    report = analyze_parser_gaps(records, top_n=10)

    assert report["parser_gap_case_count"] == 0
    assert report["top_parser_missing_params"] == []


def test_analyze_parser_gaps_ignores_item_length_cue_without_numeric_value():
    records = [
        {
            "province": "测试省",
            "bill_name": "预制钢筋混凝土管桩",
            "bill_text": "预制钢筋混凝土管桩 沉桩长度:详见图纸及地勘报告",
            "expected_quota_names": ["压预制管桩 桩深30m以内"],
            "is_match": False,
            "oracle_in_candidates": True,
        }
    ]

    report = analyze_parser_gaps(records, top_n=10)

    assert report["parser_gap_case_count"] == 0
    assert report["top_parser_missing_params"] == []
