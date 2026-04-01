from tools.diagnose_province_focus import (
    build_province_focus_report,
    resolve_target_province,
)


def _sample_records():
    return [
        {
            "province": "江西省通用安装工程消耗量定额及统一基价表(2017)",
            "bill_name": "KL",
            "bill_text": "KL 控制电缆",
            "expected_quota_ids": ["C4-2-1"],
            "expected_quota_names": ["控制电缆敷设"],
            "predicted_quota_id": "C4-9-9",
            "predicted_quota_name": "电缆中间头",
            "cause": "synonym_gap",
            "oracle_in_candidates": False,
            "miss_category": "recall_miss",
        },
        {
            "province": "江西省通用安装工程消耗量定额及统一基价表(2017)",
            "bill_name": "配电箱（半周长 2.5m以内）",
            "bill_text": "配电箱（半周长 2.5m以内） 成套 安装 调试",
            "expected_quota_ids": ["4-2-78"],
            "expected_quota_names": ["成套配电箱安装 悬挂、嵌入式(半周长) 2.5m"],
            "predicted_quota_id": "4-13-177",
            "predicted_quota_name": "接线箱暗装 半周长(mm) ≤2500",
            "cause": "wrong_tier",
            "oracle_in_candidates": True,
            "miss_category": "rank_miss",
            "candidate_snapshots": [
                {
                    "quota_id": "4-2-78",
                    "name": "成套配电箱安装 悬挂、嵌入式(半周长) 2.5m",
                    "param_score": 0.52,
                    "logic_score": 1.0,
                    "ltr_score": 0.28,
                    "candidate_canonical_features": {"entity": "配电箱", "canonical_name": "配电箱"},
                },
                {
                    "quota_id": "4-13-177",
                    "name": "接线箱暗装 半周长(mm) ≤2500",
                    "param_score": 0.65,
                    "logic_score": 1.0,
                    "ltr_score": 0.39,
                    "candidate_canonical_features": {"entity": "接线盒", "canonical_name": "接线盒"},
                },
            ],
        },
        {
            "province": "江西省通用安装工程消耗量定额及统一基价表(2017)",
            "bill_name": "铜芯电缆 ZR-YJV 5x16",
            "bill_text": "室内敷设铜芯电缆 ZR-YJV 5x16",
            "expected_quota_ids": ["4-5-16"],
            "expected_quota_names": ["室内敷设电力电缆 铜芯电力电缆敷设 电缆截面(mm2) ≤16 实际电缆芯数(芯):5"],
            "predicted_quota_id": "4-5-1",
            "predicted_quota_name": "室内敷设电力电缆 铜芯电力电缆敷设 电缆截面(mm2) ≤1",
            "cause": "wrong_tier",
            "oracle_in_candidates": True,
            "miss_category": "rank_miss",
            "candidate_snapshots": [
                {
                    "quota_id": "4-5-16",
                    "name": "室内敷设电力电缆 铜芯电力电缆敷设 电缆截面(mm2) ≤16 实际电缆芯数(芯):5",
                    "param_score": 0.91,
                    "logic_score": 1.0,
                    "ltr_score": 0.24,
                    "candidate_canonical_features": {"entity": "电缆", "canonical_name": "电缆"},
                },
                {
                    "quota_id": "4-5-1",
                    "name": "室内敷设电力电缆 铜芯电力电缆敷设 电缆截面(mm2) ≤1",
                    "param_score": 0.35,
                    "logic_score": 0.2,
                    "ltr_score": 0.31,
                    "candidate_canonical_features": {"entity": "电缆", "canonical_name": "电缆"},
                },
            ],
        },
        {
            "province": "江西省通用安装工程消耗量定额及统一基价表(2017)",
            "bill_name": "柔性抗震铸铁排水管 DN200",
            "bill_text": "柔性抗震铸铁排水管 DN200",
            "expected_quota_ids": ["C10-3-2"],
            "expected_quota_names": ["室外铸铁排水管(胶圈接口) 公称直径(mm以内) 200"],
            "predicted_quota_id": "C1-7-1",
            "predicted_quota_name": "铸铁平台 方型平台 支架上",
            "cause": "wrong_book",
            "oracle_in_candidates": False,
            "miss_category": "recall_miss",
        },
        {
            "province": "广东省通用安装工程综合定额(2018)",
            "bill_name": "单相插座",
            "bill_text": "单相插座",
            "expected_quota_ids": ["C4-5-1"],
            "expected_quota_names": ["单相带接地 暗插座电流(A) ≤15"],
            "predicted_quota_id": "C4-5-1",
            "predicted_quota_name": "单相带接地 暗插座电流(A) ≤15",
            "oracle_in_candidates": True,
        },
    ]


def test_resolve_target_province_prefers_exact_and_contains():
    records = _sample_records()

    assert (
        resolve_target_province(records, province="江西省通用安装工程消耗量定额及统一基价表(2017)")
        == "江西省通用安装工程消耗量定额及统一基价表(2017)"
    )
    assert resolve_target_province(records, province="", contains_terms=["江西", "安装"]) == "江西省通用安装工程消耗量定额及统一基价表(2017)"


def test_build_province_focus_report_splits_recall_rank_and_wrong_book():
    report = build_province_focus_report(
        _sample_records(),
        province="江西省通用安装工程消耗量定额及统一基价表(2017)",
        priority_context={"rate": 8.0, "recall_miss_total": 79},
        top_n=5,
    )

    assert report["summary"]["sample_total"] == 4
    assert report["summary"]["error_total"] == 4
    assert report["summary"]["recall_miss_total"] == 2
    assert report["summary"]["rank_miss_total"] == 2
    assert report["summary"]["wrong_book_total"] == 1
    assert report["recall"]["category_counts"] == {
        "synonym_gap": 1,
        "rule_mislead": 1,
    }
    assert report["ranking"]["entity_confusion_total"] == 1
    assert report["ranking"]["same_entity_wrong_tier_total"] == 1
    assert report["ranking"]["expected_param_better_but_lost"] == 1
    assert report["ranking"]["expected_logic_better_but_lost"] == 1
    assert report["wrong_book"]["top_transitions"][0]["key"] == "C1 -> C10"
    assert [row["category"] for row in report["priority_actions"]] == ["monitor_only"]
