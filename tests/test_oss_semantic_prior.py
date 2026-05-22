import json

import config
from src.hybrid_searcher import HybridSearcher
import src.oss_semantic_prior as oss_prior
from src.oss_semantic_prior import OssSemanticPriorSource


def _write_shadow(path):
    path.write_text(
        json.dumps(
            {
                "target_group": "R2_like",
                "bucket": "correct_very_low_in_snapshot",
                "province": "Test Province",
                "bill_name": "配电箱 1AP1",
                "bill_core": "配电箱",
                "target_feature_snapshot": {
                    "canonical_name": "配电箱",
                    "family": "electrical_box",
                    "entity": "配电箱",
                    "system": "电气",
                },
                "top_candidates": [
                    {
                        "quota_id": "T-1",
                        "name": "成套配电箱安装 悬挂、嵌入式(半周长) 1.5m",
                        "score": 7.5,
                        "why": ["canonical_name", "entity", "family"],
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def test_oss_semantic_prior_source_is_no_store_shadow(tmp_path, monkeypatch):
    common_dir = tmp_path / "common"
    shadow_path = tmp_path / "oss_shadow.jsonl"
    _write_shadow(shadow_path)
    monkeypatch.setattr(config, "COMMON_DB_DIR", common_dir)

    source = OssSemanticPriorSource(shadow_path)
    candidates = source.collect(
        province="Test Province",
        query_text="配电箱 1AP1",
        item={"name": "配电箱 1AP1"},
        top_k=3,
    )

    assert [candidate["quota_id"] for candidate in candidates] == ["T-1"]
    assert candidates[0]["match_source"] == "oss_semantic_prior_shadow"
    assert candidates[0]["oss_semantic_prior_decision_authority"] is False
    assert candidates[0]["knowledge_prior_sources"] == ["oss_semantic_prior_shadow"]
    assert candidates[0]["candidate_canonical_features"]["family"] == "electrical_box"
    assert not (common_dir / "candidate_features.db").exists()


def test_hybrid_searcher_collects_oss_semantic_prior_without_writing_feature_db(
    tmp_path,
    monkeypatch,
):
    common_dir = tmp_path / "common"
    shadow_path = tmp_path / "oss_shadow.jsonl"
    _write_shadow(shadow_path)
    monkeypatch.setattr(config, "COMMON_DB_DIR", common_dir)
    monkeypatch.setattr(config, "OSS_SEMANTIC_PRIOR_ENABLED", True)
    monkeypatch.setattr(config, "OSS_SEMANTIC_PRIOR_SHADOW_PATH", shadow_path)
    monkeypatch.setattr(config, "OSS_SEMANTIC_PRIOR_TOP_K", 3)
    monkeypatch.setattr(config, "SEARCH_EXPERIENCE_INJECTION_ENABLED", False)
    monkeypatch.setattr(config, "SEARCH_UNIVERSAL_KB_INJECTION_ENABLED", False)
    monkeypatch.setattr(config, "SEARCH_UNIFIED_DATA_PRIOR_ENABLED", False, raising=False)
    monkeypatch.setattr(oss_prior, "_SOURCE", None)
    monkeypatch.setattr(
        HybridSearcher,
        "_collect_quota_alias_exact_prior_candidates",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        HybridSearcher,
        "_collect_quota_name_fallback_prior_candidates",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        HybridSearcher,
        "_collect_quota_id_neighbor_prior_candidates",
        lambda *args, **kwargs: [],
    )

    searcher = HybridSearcher(province="Test Province", unified_data_layer=False)
    candidates = searcher.collect_prior_candidates(
        "配电箱 1AP1",
        full_query="配电箱 1AP1",
        item={"name": "配电箱 1AP1"},
        top_k=3,
    )

    assert [candidate["quota_id"] for candidate in candidates] == ["T-1"]
    assert candidates[0]["match_source"] == "oss_semantic_prior_shadow"
    assert candidates[0]["oss_semantic_prior_decision_authority"] is False
    assert not (common_dir / "candidate_features.db").exists()
