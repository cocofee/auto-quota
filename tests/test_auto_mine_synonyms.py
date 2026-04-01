from tools.auto_mine_synonyms import (
    DEFAULT_OUTPUT,
    build_arg_parser,
    build_candidate_report,
    build_risk_flags,
    collect_benchmark_candidates_from_report,
    merge_candidate_maps,
)


def test_collect_benchmark_candidates_from_report_parses_rows():
    report = {
        "recall_gaps": {
            "top_missing_synonyms": [
                {
                    "key": "控制电缆 -> 一般电缆 电缆14芯以下",
                    "count": 3,
                    "source_term": "控制电缆",
                    "target_term": "一般电缆 电缆14芯以下",
                    "provinces": ["广东", "江西"],
                    "examples": [{"bill_name": "控制电缆"}],
                }
            ]
        }
    }

    candidates = collect_benchmark_candidates_from_report(report, min_count=2)

    row = candidates[("控制电缆", "一般电缆 电缆14芯以下")]
    assert row["benchmark_count"] == 3
    assert row["sources"] == {"benchmark"}
    assert row["provinces"] == ["广东", "江西"]


def test_merge_candidate_maps_merges_sources_and_filters_existing_pairs():
    benchmark_candidates = {
        ("控制电缆", "一般电缆 电缆14芯以下"): {
            "source_term": "控制电缆",
            "target_term": "一般电缆 电缆14芯以下",
            "sources": {"benchmark"},
            "benchmark_count": 4,
            "examples": [{"bill_name": "控制电缆"}],
            "provinces": ["广东"],
        }
    }
    experience_candidates = {
        ("控制电缆", "一般电缆 电缆14芯以下"): {
            "source_term": "控制电缆",
            "target_term": "一般电缆 电缆14芯以下",
            "sources": {"experience"},
            "benchmark_count": 0,
            "examples": [],
            "provinces": [],
        },
        ("配电箱", "配电箱安装"): {
            "source_term": "配电箱",
            "target_term": "配电箱安装",
            "sources": {"experience"},
            "benchmark_count": 0,
            "examples": [],
            "provinces": [],
        },
    }
    bill_candidates = {
        ("控制电缆", "一般电缆 电缆14芯以下"): {
            "source_term": "控制电缆",
            "target_term": "一般电缆 电缆14芯以下",
            "sources": {"bill_library"},
            "benchmark_count": 0,
            "examples": [],
            "provinces": [],
        }
    }

    merged = merge_candidate_maps(
        [benchmark_candidates, experience_candidates, bill_candidates],
        existing_pairs={("配电箱", "配电箱安装")},
    )

    assert len(merged) == 1
    row = merged[0]
    assert row["source_term"] == "控制电缆"
    assert row["target_term"] == "一般电缆 电缆14芯以下"
    assert row["sources"] == ["benchmark", "bill_library", "experience"]
    assert row["benchmark_count"] == 4
    assert row["score"] > 400


def test_build_candidate_report_keeps_top_n_and_meta():
    report = build_candidate_report(
        benchmark_report={
            "recall_gaps": {
                "top_missing_synonyms": [
                    {
                        "source_term": "控制电缆",
                        "target_term": "一般电缆 电缆14芯以下",
                        "count": 3,
                    },
                    {
                        "source_term": "装饰灯",
                        "target_term": "荧光灯光沿",
                        "count": 2,
                    },
                ]
            }
        },
        experience_mapping={"控制电缆": ["一般电缆 电缆14芯以下"]},
        bill_mapping={"装饰灯": "荧光灯光沿"},
        existing_pairs=set(),
        benchmark_min_count=2,
        top_n=1,
    )

    assert report["meta"]["candidate_pool_size"] == 2
    assert len(report["candidates"]) == 1
    assert report["candidates"][0]["source_term"] in {"控制电缆", "装饰灯"}
    assert report["benchmark_priority_candidates"] == []


def test_build_risk_flags_marks_component_target():
    flags = build_risk_flags("空调器", "隔振垫")

    assert "component_target" in flags


def test_build_risk_flags_marks_weak_lexical_overlap():
    flags = build_risk_flags("混凝土井", "沟槽回填 塘碴")

    assert "weak_lexical_overlap" in flags


def test_build_candidate_report_excludes_non_synonym_benchmark_pairs_from_review_list():
    report = build_candidate_report(
        benchmark_report={
            "recall_gaps": {
                "top_missing_synonyms": [
                    {
                        "source_term": "空调器",
                        "target_term": "隔振垫",
                        "count": 6,
                    },
                    {
                        "source_term": "控制电缆",
                        "target_term": "一般电缆 电缆14芯以下",
                        "count": 3,
                    },
                ]
            }
        },
        experience_mapping={},
        bill_mapping={},
        existing_pairs=set(),
        benchmark_min_count=2,
        top_n=10,
    )

    pairs = {(row["source_term"], row["target_term"]) for row in report["benchmark_priority_candidates"]}
    assert ("空调器", "隔振垫") not in pairs
