import json
import shutil
from pathlib import Path

from tools.run_benchmark import (
    _build_benchmark_asset_buckets,
    export_benchmark_assets,
    materialize_benchmark_learning_outputs,
)


def _sample_json_results():
    return [
        {
            "province": "测试安装定额",
            "details": [
                {
                    "is_match": False,
                    "cause": "wrong_tier",
                    "bill_name": "配管",
                    "bill_text": "JDG20 配管",
                    "specialty": "C4",
                    "stored_ids": ["C4-1-1"],
                    "stored_names": ["正确定额"],
                    "algo_id": "C4-1-2",
                    "algo_name": "错误档位定额",
                    "confidence": 62,
                    "oracle_in_candidates": True,
                    "all_candidate_ids": ["C4-1-2", "C4-1-1"],
                    "alternatives": [
                        {"quota_id": "C4-1-1", "name": "正确定额", "score": 0.91},
                        {"quota_id": "C4-1-2", "name": "错误档位定额", "score": 0.88},
                    ],
                    "reasoning_decision": {"stage": "validator"},
                    "trace_path": ["canonical", "retriever", "validator"],
                    "match_source": "search",
                    "no_match_reason": "",
                    "candidate_count": 2,
                    "pre_ltr_top1_id": "C4-1-2",
                    "post_ltr_top1_id": "C4-1-1",
                    "post_arbiter_top1_id": "C4-1-2",
                    "post_final_top1_id": "C4-1-2",
                    "final_changed_by": "arbiter",
                    "miss_stage": "post_rank_miss",
                    "error_stage": "candidate_arbiter",
                    "error_type": "post_ltr_correct_but_arbiter_changed",
                    "candidate_snapshots": [
                        {"quota_id": "C4-1-2", "name": "错误档位定额"},
                        {"quota_id": "C4-1-1", "name": "正确定额"},
                    ],
                },
                {
                    "is_match": False,
                    "cause": "synonym_gap",
                    "bill_name": "KL",
                    "bill_text": "KL 控制电缆",
                    "specialty": "C4",
                    "stored_ids": ["C4-2-1"],
                    "stored_names": ["控制电缆敷设"],
                    "algo_id": "C4-9-9",
                    "algo_name": "电缆中间头",
                    "confidence": 21,
                    "oracle_in_candidates": False,
                    "all_candidate_ids": ["C4-9-9"],
                    "alternatives": [
                        {"quota_id": "C4-9-9", "name": "电缆中间头", "score": 0.5},
                    ],
                    "reasoning_decision": {},
                    "trace_path": ["canonical", "retriever"],
                    "match_source": "search",
                    "no_match_reason": "synonym gap",
                    "error_stage": "retriever",
                    "error_type": "oracle_not_in_candidates",
                },
                {
                    "is_match": False,
                    "cause": "wrong_book",
                    "bill_name": "给水管",
                    "bill_text": "PP-R 给水管 De50",
                    "specialty": "C10",
                    "stored_ids": ["C10-3-2"],
                    "stored_names": ["给排水管道安装"],
                    "algo_id": "C8-7-1",
                    "algo_name": "市政管道",
                    "confidence": 33,
                    "oracle_in_candidates": False,
                    "all_candidate_ids": ["C8-7-1"],
                    "alternatives": [],
                    "reasoning_decision": {},
                    "trace_path": ["router", "retriever"],
                    "match_source": "search",
                    "no_match_reason": "wrong specialty",
                    "error_stage": "retriever",
                    "error_type": "oracle_not_in_candidates",
                },
                {
                    "is_match": True,
                    "cause": "",
                    "bill_name": "应跳过",
                    "bill_text": "",
                    "specialty": "C4",
                },
            ],
        }
    ]


def test_build_benchmark_asset_buckets_groups_records():
    buckets = _build_benchmark_asset_buckets(_sample_json_results())

    assert len(buckets["all_errors"]) == 3
    assert len(buckets["rerank_pairs"]) == 1
    assert len(buckets["synonym_gaps"]) == 1
    assert len(buckets["route_errors"]) == 1
    assert len(buckets["tier_errors"]) == 1

    rerank_pair = buckets["rerank_pairs"][0]
    assert rerank_pair["positive_quota_ids"] == ["C4-1-1"]
    assert rerank_pair["negative_quota_id"] == "C4-1-2"
    assert rerank_pair["retrieved_candidates"][0]["quota_id"] == "C4-1-2"


def test_build_benchmark_asset_buckets_keeps_top10_rerank_candidates():
    json_results = [
        {
            "province": "测试安装定额",
            "details": [
                {
                    "is_match": False,
                    "cause": "wrong_tier",
                    "bill_name": "配管",
                    "bill_text": "JDG20 配管",
                    "specialty": "C4",
                    "stored_ids": ["C4-1-1"],
                    "stored_names": ["正确定额"],
                    "algo_id": "C4-1-99",
                    "algo_name": "错误定额",
                    "confidence": 62,
                    "oracle_in_candidates": True,
                    "all_candidate_ids": [f"C4-1-{i}" for i in range(1, 16)],
                    "alternatives": [
                        {"quota_id": f"C4-1-{i}", "name": f"候选{i}", "reasoning": {"detail": f"detail-{i}"}}
                        for i in range(1, 13)
                    ],
                    "reasoning_decision": {"stage": "validator"},
                    "trace_path": ["canonical", "retriever", "validator"],
                    "match_source": "search",
                    "no_match_reason": "",
                }
            ],
        }
    ]

    buckets = _build_benchmark_asset_buckets(json_results)

    rerank_pair = buckets["rerank_pairs"][0]
    assert len(rerank_pair["retrieved_candidates"]) == 10
    assert rerank_pair["retrieved_candidates"][1]["reasoning"]["detail"] == "detail-1"


def test_export_benchmark_assets_writes_manifest_and_jsonl():
    temp_root = Path("output/_tmp_benchmark_assets")
    shutil.rmtree(temp_root, ignore_errors=True)
    try:
        asset_dir = export_benchmark_assets(_sample_json_results(), str(temp_root / "assets"))

        assert asset_dir == temp_root / "assets"
        manifest = json.loads((asset_dir / "manifest.json").read_text(encoding="utf-8"))

        assert manifest["counts"]["all_errors"] == 3
        assert manifest["counts"]["rerank_pairs"] == 1
        assert manifest["counts"]["synonym_gaps"] == 1
        assert manifest["counts"]["route_errors"] == 1
        assert manifest["counts"]["tier_errors"] == 1

        tier_lines = (asset_dir / "tier_errors.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(tier_lines) == 1
        tier_record = json.loads(tier_lines[0])
        assert tier_record["cause"] == "wrong_tier"
        assert tier_record["trace_path"] == ["canonical", "retriever", "validator"]
        assert tier_record["post_ltr_top1_id"] == "C4-1-1"
        assert tier_record["candidate_count"] == 2
        assert tier_record["error_stage"] == "candidate_arbiter"
        assert tier_record["error_type"] == "post_ltr_correct_but_arbiter_changed"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_materialize_benchmark_learning_outputs_builds_knowledge_and_training():
    temp_root = Path("output/_tmp_benchmark_learning_outputs")
    shutil.rmtree(temp_root, ignore_errors=True)
    try:
        asset_dir = export_benchmark_assets(_sample_json_results(), str(temp_root / "assets"))
        outputs = materialize_benchmark_learning_outputs(
            asset_dir,
            knowledge_out=temp_root / "generated" / "knowledge.json",
            digest_out=temp_root / "generated" / "knowledge_digest.json",
            digest_md_out=temp_root / "generated" / "knowledge_digest.md",
            training_out_root=temp_root / "training",
        )

        knowledge_path = Path(outputs["knowledge_path"])
        digest_path = Path(outputs["digest_path"])
        training_manifest_path = Path(outputs["training_manifest_path"])

        assert knowledge_path.exists()
        assert digest_path.exists()
        assert training_manifest_path.exists()

        knowledge = json.loads(knowledge_path.read_text(encoding="utf-8"))
        assert knowledge["provinces"]

        training_manifest = json.loads(training_manifest_path.read_text(encoding="utf-8"))
        assert training_manifest["counts"]["rerank"] >= 1
        assert training_manifest["counts"]["route"] >= 1
        assert training_manifest["counts"]["tier"] >= 1
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
