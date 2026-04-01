import json
import shutil
from pathlib import Path

from tools.build_benchmark_knowledge import build_knowledge_from_asset_root


def test_national_route_and_tier_need_multi_province_support():
    temp_root = Path("output/_tmp_benchmark_knowledge_threshold_single")
    shutil.rmtree(temp_root, ignore_errors=True)
    run_dir = temp_root / "20260320_010203"
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
        (run_dir / "route_errors.jsonl").write_text(
            json.dumps({
                "province": "单省安装",
                "bill_name": "配电箱",
                "expected_book": "C4",
                "specialty": "C4",
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (run_dir / "tier_errors.jsonl").write_text(
            json.dumps({
                "province": "单省安装",
                "bill_name": "配电箱",
                "expected_quota_names": ["成套配电箱安装 悬挂、嵌入式"],
                "predicted_quota_name": "成套配电箱安装 落地式",
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        knowledge = build_knowledge_from_asset_root(temp_root)
        assert knowledge["national"]["route_biases"] == {}
        assert knowledge["national"]["tier_hints"] == {}
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_national_route_and_tier_promote_with_multi_province_support():
    temp_root = Path("output/_tmp_benchmark_knowledge_threshold_multi")
    shutil.rmtree(temp_root, ignore_errors=True)
    run_a = temp_root / "20260320_010203"
    run_b = temp_root / "20260320_020304"
    run_a.mkdir(parents=True, exist_ok=True)
    run_b.mkdir(parents=True, exist_ok=True)
    try:
        for run_dir, province in ((run_a, "甲省安装"), (run_b, "乙省安装")):
            (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
            (run_dir / "route_errors.jsonl").write_text(
                json.dumps({
                    "province": province,
                    "bill_name": "配电箱",
                    "expected_book": "C4",
                    "specialty": "C4",
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (run_dir / "tier_errors.jsonl").write_text(
                json.dumps({
                    "province": province,
                    "bill_name": "配电箱",
                    "expected_quota_names": ["成套配电箱安装 悬挂、嵌入式"],
                    "predicted_quota_name": "成套配电箱安装 落地式",
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        knowledge = build_knowledge_from_asset_root(temp_root)
        assert knowledge["national"]["route_biases"]["配电箱"]["preferred_books"] == ["C4"]
        assert knowledge["national"]["tier_hints"]["配电箱"]["preferred_quota_names"] == ["成套配电箱安装 悬挂、嵌入式"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
