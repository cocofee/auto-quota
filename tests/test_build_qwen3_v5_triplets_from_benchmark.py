import json
import shutil
from pathlib import Path

from tools.build_qwen3_v5_triplets_from_benchmark import (
    build_triplets_from_asset_root,
    export_triplets,
)


def test_build_triplets_from_asset_root_prioritizes_recall_and_dedupes():
    temp_root = Path("output/_tmp_qwen3_v5_triplets")
    run_dir = temp_root / "20260324_2212_full_qwen3_v4"
    shutil.rmtree(temp_root, ignore_errors=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        rows = [
            {
                "province": "测试省份",
                "specialty": "C4",
                "bill_name": "桥架",
                "bill_text": "钢制桥架 400x100",
                "cause": "synonym_gap",
                "oracle_in_candidates": False,
                "expected_quota_ids": ["C4-1"],
                "expected_quota_names": ["钢制槽式桥架 400"],
                "predicted_quota_id": "C4-2",
                "predicted_quota_name": "铝合金桥架 200",
                "retrieved_candidates": [
                    {"quota_id": "C4-2", "name": "铝合金桥架 200"},
                    {"quota_id": "C4-3", "name": "玻璃钢桥架 200"},
                ],
                "trace_path": ["search_select"],
            },
            {
                "province": "测试省份",
                "specialty": "C10",
                "bill_name": "配电箱",
                "bill_text": "明装配电箱 4回路",
                "cause": "wrong_tier",
                "oracle_in_candidates": True,
                "expected_quota_ids": ["C10-1"],
                "expected_quota_names": ["明装配电箱 4回路"],
                "predicted_quota_id": "C10-2",
                "predicted_quota_name": "明装配电箱 8回路",
                "retrieved_candidates": [
                    {"quota_id": "C10-2", "name": "明装配电箱 8回路"},
                    {"quota_id": "C10-1", "name": "明装配电箱 4回路"},
                    {"quota_id": "C10-3", "name": "暗装配电箱 4回路"},
                ],
                "trace_path": ["search_select", "final_validate"],
            },
        ]
        with (run_dir / "all_errors.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

        triplets, recall_triplets, manifest = build_triplets_from_asset_root(temp_root)

        assert len(triplets) == 4
        assert len(recall_triplets) == 2
        assert manifest["source_type_counts"]["recall_miss"] == 2
        assert manifest["source_type_counts"]["ranking_error"] == 2
        assert all(item["negative"] != "明装配电箱 4回路" for item in triplets)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_export_triplets_writes_full_recall_and_manifest():
    temp_root = Path("output/_tmp_qwen3_v5_triplets_export")
    run_dir = temp_root / "assets" / "20260324_2212_full_qwen3_v4"
    out_dir = temp_root / "data"
    shutil.rmtree(temp_root, ignore_errors=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        row = {
            "province": "测试省份",
            "specialty": "C4",
            "bill_name": "风管",
            "bill_text": "柔性软风管",
            "cause": "synonym_gap",
            "oracle_in_candidates": False,
            "expected_quota_ids": ["C4-9"],
            "expected_quota_names": ["柔性接口及伸缩节"],
            "predicted_quota_id": "C4-10",
            "predicted_quota_name": "碳钢调节阀安装",
            "retrieved_candidates": [
                {"quota_id": "C4-10", "name": "碳钢调节阀安装"},
            ],
        }
        (run_dir / "all_errors.jsonl").write_text(
            json.dumps(row, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        manifest = export_triplets(
            asset_root=temp_root / "assets",
            output_path=out_dir / "triplets.jsonl",
            recall_only_path=out_dir / "recall_only.jsonl",
            manifest_path=out_dir / "manifest.json",
        )

        assert manifest["counts"]["triplets"] == 1
        assert (out_dir / "triplets.jsonl").exists()
        assert (out_dir / "recall_only.jsonl").exists()
        assert (out_dir / "manifest.json").exists()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
