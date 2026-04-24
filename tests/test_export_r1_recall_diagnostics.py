from __future__ import annotations

from tools.export_r1_recall_diagnostics import (
    build_r1_recall_diagnostics,
    classify_r1_bucket,
)


def _detail(**overrides):
    base = {
        "bill_id": "1",
        "bill_name": "混凝土井",
        "specialty": "C6",
        "stored_ids": ["6-311"],
        "algo_id": "10-1",
        "is_match": False,
        "recall_rank": -1,
        "candidate_snapshots": [
            {"quota_id": "10-1", "name": "错误候选"},
            {"quota_id": "6-276", "name": "另一个候选"},
        ],
        "recall_topk_ids": ["10-1", "6-276"],
        "no_match_reason": "",
    }
    base.update(overrides)
    return base


def test_classify_r1_bucket_normalizes_c_prefixes():
    assert classify_r1_bucket(_detail(specialty="C6", stored_ids=["6-311"], candidate_snapshots=[{}, {}, {}, {}])) == "semantic_candidate_pool_miss"


def test_classify_r1_bucket_detects_major_buckets():
    assert classify_r1_bucket(_detail(no_match_reason="all candidates rejected by hard parameter validation")) == "hard_param_reject"
    assert classify_r1_bucket(_detail(no_match_reason="搜索无匹配结果")) == "search_no_result"
    assert classify_r1_bucket(_detail(no_match_reason="缺少专业/上下文信息，且清单名称过短、描述过弱，转人工审核")) == "weak_context_manual_review"
    assert classify_r1_bucket(_detail(specialty="")) == "missing_specialty_context"
    assert classify_r1_bucket(_detail(specialty="C9", stored_ids=["C10-3-98"])) == "real_specialty_route_mismatch"
    assert classify_r1_bucket(_detail(candidate_snapshots=[{"quota_id": "A"}])) == "thin_candidate_pool"


def test_build_r1_recall_diagnostics_exports_rows_and_summary():
    payload = {
        "results": [
            {
                "province": "浙江省市政工程预算定额(2018)",
                "details": [
                    _detail(),
                    _detail(bill_id="2", is_match=True),
                    _detail(bill_id="3", recall_rank=2),
                    _detail(
                        bill_id="4",
                        bill_name="抛光砖楼面",
                        specialty="",
                        stored_ids=["A1-12-75"],
                        candidate_snapshots=[{"quota_id": "8005908", "name": "楼地面候选"}],
                    ),
                ],
            }
        ]
    }

    rows, summary = build_r1_recall_diagnostics(payload)

    assert summary["r1_total"] == 2
    assert summary["bucket_counts"] == {
        "missing_specialty_context": 1,
        "thin_candidate_pool": 1,
    }
    assert rows[0]["province"] == "浙江省市政工程预算定额(2018)"
    assert rows[0]["top_candidate_ids"]
    assert rows[0]["recall_topk_count"] == 2
