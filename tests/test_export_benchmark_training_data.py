import json
import shutil
from pathlib import Path

from tools.export_benchmark_training_data import (
    build_training_datasets,
    export_training_datasets,
)


def test_build_training_datasets_collects_legacy_rows_and_cgr_rows():
    temp_root = Path("output/_tmp_benchmark_training")
    shutil.rmtree(temp_root, ignore_errors=True)
    run_dir = temp_root / "20260320_010203"
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        (run_dir / "rerank_pairs.jsonl").write_text(
            json.dumps({
                "province": "Beijing Install 2024",
                "specialty": "C4",
                "bill_name": "Conduit",
                "bill_text": "JDG20 conduit",
                "positive_quota_ids": ["C4-1-1"],
                "positive_quota_names": ["Correct quota"],
                "negative_quota_id": "C4-1-2",
                "negative_quota_name": "Wrong quota",
                "retrieved_candidates": [{"quota_id": "C4-1-2"}],
                "trace_path": ["canonical", "retriever", "validator"],
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (run_dir / "route_errors.jsonl").write_text(
            json.dumps({
                "province": "Beijing Install 2024",
                "specialty": "C10",
                "bill_name": "Water pipe",
                "bill_text": "PPR water pipe De50",
                "expected_book": "C10",
                "predicted_book": "C8",
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (run_dir / "tier_errors.jsonl").write_text(
            json.dumps({
                "province": "Beijing Install 2024",
                "specialty": "C4",
                "bill_name": "Socket outlet",
                "bill_text": "5-hole outlet",
                "expected_quota_names": ["Correct outlet quota"],
                "predicted_quota_name": "Wrong outlet quota",
                "trace_path": ["canonical", "retriever", "validator"],
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (run_dir / "all_errors.jsonl").write_text(
            json.dumps({
                "province": "Beijing Install 2024",
                "specialty": "C10",
                "bill_name": "Pipe support",
                "bill_text": "Galv steel pipe DN25 exposed",
                "cause": "wrong_tier",
                "expected_quota_ids": ["C10-1-1"],
                "expected_quota_names": ["Galv steel pipe DN25 exposed"],
                "predicted_quota_id": "C10-1-2",
                "predicted_quota_name": "Galv steel pipe DN50 exposed",
                "oracle_in_candidates": True,
                "candidate_count": 2,
                "trace_path": ["canonical", "retriever", "validator"],
                "candidate_snapshots": [
                    {
                        "quota_id": "C10-1-2",
                        "name": "Galv steel pipe DN50 exposed",
                        "param_match": False,
                        "param_score": 0.55,
                        "logic_score": 0.52,
                        "feature_alignment_score": 0.88,
                        "context_alignment_score": 0.85,
                        "hybrid_score": 0.95,
                        "rerank_score": 0.93,
                        "semantic_rerank_score": 0.94,
                        "spec_rerank_score": 0.90,
                        "family_gate_score": 1.1,
                        "candidate_canonical_features": {
                            "family": "pipe_support",
                            "entity": "pipe",
                            "material": "galv_steel",
                            "install_method": "exposed",
                            "system": "water",
                        },
                        "_ltr_param": {
                            "param_main_exact": 0,
                            "param_main_rel_dist": 0.5,
                            "param_main_direction": 1,
                            "param_material_match": 1.0,
                        },
                    },
                    {
                        "quota_id": "C10-1-1",
                        "name": "Galv steel pipe DN25 exposed",
                        "param_match": True,
                        "param_score": 0.96,
                        "logic_score": 0.95,
                        "feature_alignment_score": 0.92,
                        "context_alignment_score": 0.91,
                        "hybrid_score": 0.22,
                        "rerank_score": 0.61,
                        "semantic_rerank_score": 0.58,
                        "spec_rerank_score": 0.65,
                        "family_gate_score": 1.2,
                        "candidate_canonical_features": {
                            "family": "pipe_support",
                            "entity": "pipe",
                            "material": "galv_steel",
                            "install_method": "exposed",
                            "system": "water",
                        },
                        "_ltr_param": {
                            "param_main_exact": 1,
                            "param_main_rel_dist": 0.0,
                            "param_main_direction": 0,
                            "param_material_match": 1.0,
                        },
                    },
                ],
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        datasets = build_training_datasets(temp_root)
        assert len(datasets["rerank"]) == 1
        assert len(datasets["route"]) == 1
        assert len(datasets["tier"]) == 1
        assert len(datasets["cgr_group"]) == 1
        assert len(datasets["cgr_accept"]) == 1

        group = datasets["cgr_group"][0]
        assert group["sample_id"] == "20260320_010203:cgr:1"
        assert group["top1_quota_id"] == "C10-1-1"
        assert group["top1_correct"] is True
        assert 0.0 <= group["gate"] <= 1.0
        assert group["candidates"][0]["quota_id"] == "C10-1-1"
        assert group["candidates"][0]["is_oracle"] == 1
        assert "group_features" in group["candidates"][0]

        accept = datasets["cgr_accept"][0]
        assert accept["accept_label"] == 1
        assert accept["top1_quota_id"] == "C10-1-1"
        assert accept["oracle_in_candidates"] is True
        assert accept["p1"] > 0.0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_export_training_datasets_writes_manifest_and_new_cgr_files():
    temp_root = Path("output/_tmp_benchmark_training_export")
    asset_dir = temp_root / "assets" / "20260320_010203"
    out_dir = temp_root / "train"
    shutil.rmtree(temp_root, ignore_errors=True)
    asset_dir.mkdir(parents=True, exist_ok=True)
    try:
        (asset_dir / "rerank_pairs.jsonl").write_text(
            json.dumps({
                "province": "Beijing Install 2024",
                "bill_name": "Conduit",
                "bill_text": "JDG20 conduit",
                "positive_quota_ids": ["C4-1-1"],
                "negative_quota_id": "C4-1-2",
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (asset_dir / "all_errors.jsonl").write_text(
            json.dumps({
                "province": "Beijing Install 2024",
                "specialty": "C4",
                "bill_name": "Conduit",
                "bill_text": "JDG20 conduit DN20",
                "cause": "wrong_tier",
                "expected_quota_ids": ["C4-1-1"],
                "expected_quota_names": ["JDG conduit DN20"],
                "predicted_quota_id": "C4-1-2",
                "predicted_quota_name": "JDG conduit DN25",
                "oracle_in_candidates": True,
                "candidate_snapshots": [
                    {
                        "quota_id": "C4-1-2",
                        "name": "JDG conduit DN25",
                        "param_match": False,
                        "param_score": 0.50,
                        "logic_score": 0.60,
                        "feature_alignment_score": 0.88,
                        "context_alignment_score": 0.82,
                        "hybrid_score": 0.92,
                        "rerank_score": 0.90,
                        "semantic_rerank_score": 0.91,
                        "spec_rerank_score": 0.87,
                        "family_gate_score": 1.0,
                        "_ltr_param": {
                            "param_main_exact": 0,
                            "param_main_rel_dist": 0.25,
                            "param_main_direction": 1,
                            "param_material_match": 1.0,
                        },
                    },
                    {
                        "quota_id": "C4-1-1",
                        "name": "JDG conduit DN20",
                        "param_match": True,
                        "param_score": 0.98,
                        "logic_score": 0.95,
                        "feature_alignment_score": 0.93,
                        "context_alignment_score": 0.90,
                        "hybrid_score": 0.20,
                        "rerank_score": 0.62,
                        "semantic_rerank_score": 0.60,
                        "spec_rerank_score": 0.63,
                        "family_gate_score": 1.1,
                        "_ltr_param": {
                            "param_main_exact": 1,
                            "param_main_rel_dist": 0.0,
                            "param_main_direction": 0,
                            "param_material_match": 1.0,
                        },
                    },
                ],
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        manifest_path, manifest = export_training_datasets(temp_root / "assets", out_dir)
        assert manifest_path == out_dir / "manifest.json"
        assert manifest["counts"]["rerank"] == 1
        assert manifest["counts"]["cgr_group"] == 1
        assert manifest["counts"]["cgr_accept"] == 1
        assert (out_dir / "rerank_train.jsonl").exists()
        assert (out_dir / "route_train.jsonl").exists()
        assert (out_dir / "tier_train.jsonl").exists()
        assert (out_dir / "cgr_group_train.jsonl").exists()
        assert (out_dir / "cgr_accept_train.jsonl").exists()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
