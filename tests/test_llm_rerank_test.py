import json
import shutil
from pathlib import Path

from tools import llm_rerank_test


def test_extract_candidates_prefers_retrieved_candidates():
    case = {
        "predicted_quota_id": "C4-1-9",
        "predicted_quota_name": "错误定额",
        "retrieved_candidates": [
            {"quota_id": "C4-1-9", "name": "错误定额", "is_selected": True},
            {
                "quota_id": "C4-1-1",
                "name": "正确定额",
                "is_selected": False,
                "reasoning": {"detail": "DN=20 精确匹配"},
            },
            {"quota_id": "C4-1-2", "name": "备选定额", "is_selected": False},
        ],
        "alternatives": [
            {"quota_id": "C4-1-3", "name": "不该优先走这里"},
        ],
    }

    candidates = llm_rerank_test.extract_candidates(case, max_candidates=3)

    assert [item["quota_id"] for item in candidates] == ["C4-1-9", "C4-1-1", "C4-1-2"]
    assert candidates[1]["param_detail"] == "DN=20 精确匹配"


def test_load_rerank_cases_supports_exported_asset_fields():
    payload = {
        "province": "北京市建设工程施工消耗量标准(2024)",
        "cause": "wrong_tier",
        "oracle_in_candidates": True,
        "expected_quota_ids": ["C4-1-1"],
        "predicted_quota_id": "C4-1-2",
        "predicted_quota_name": "错误定额",
        "retrieved_candidates": [
            {"quota_id": "C4-1-2", "name": "错误定额", "is_selected": True},
            {"quota_id": "C4-1-1", "name": "正确定额", "is_selected": False},
        ],
    }
    temp_root = Path("output/_tmp_llm_rerank_test")
    shutil.rmtree(temp_root, ignore_errors=True)
    try:
        temp_root.mkdir(parents=True, exist_ok=True)
        path = temp_root / "all_errors.jsonl"
        path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

        cases = llm_rerank_test.load_rerank_cases(str(path), province_filter="北京", max_cases=20)

        assert len(cases) == 1
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_build_prompt_returns_candidate_map_and_params():
    prompt, candidate_map = llm_rerank_test.build_prompt(
        "JDG20 配管 材质:钢管 规格:DN20 连接方式:丝接",
        [
            {"quota_id": "C4-1-2", "name": "错误定额", "param_detail": "DN<=25"},
            {"quota_id": "C4-1-1", "name": "正确定额"},
        ],
    )

    assert "JDG20 配管" in prompt
    assert "dn=20" in prompt.lower()
    assert "material=钢管" in prompt
    assert "connection=丝接" in prompt
    assert "1. [C4-1-2] 错误定额 | DN<=25" in prompt
    assert candidate_map == {"1": "C4-1-2", "2": "C4-1-1"}
