from __future__ import annotations

import json
import shutil
from pathlib import Path

from tools.cgr_train import train_cgr_model, train_cgr_model_from_files


def _group_row(sample_id: str, split: str, top_is_oracle: bool) -> dict:
    return {
        "sample_id": sample_id,
        "split": split,
        "oracle_quota_ids": ["Q1"],
        "query_summary": {
            "route": "installation_spec",
            "family_confidence": 0.9,
            "query_param_coverage": 0.8,
            "group_ambiguity_score": 0.2,
            "candidate_count": 2,
            "has_material": 1,
            "has_install_method": 1,
        },
        "candidates": [
            {
                "quota_id": "Q1" if top_is_oracle else "Q2",
                "is_oracle": 1 if top_is_oracle else 0,
                "cgr_feasible": True,
                "cgr_prior_score": 0.02,
                "cgr_tier_penalty": 0.0 if top_is_oracle else 0.4,
                "cgr_generic_penalty": 0.1,
                "cgr_soft_conflict_penalty": 0.0 if top_is_oracle else 0.2,
                "cgr_sem_score": 0.4 if top_is_oracle else 0.8,
                "cgr_str_score": 0.9 if top_is_oracle else 0.2,
                "rerank_score": 0.45 if top_is_oracle else 0.92,
                "param_score": 0.96 if top_is_oracle else 0.42,
                "logic_score": 0.94 if top_is_oracle else 0.40,
                "feature_alignment_score": 0.92 if top_is_oracle else 0.70,
                "context_alignment_score": 0.90 if top_is_oracle else 0.65,
                "_ltr_param": {
                    "param_main_rel_dist": 0.0 if top_is_oracle else 0.5,
                    "param_main_exact": 1 if top_is_oracle else 0,
                    "param_material_match": 1.0,
                },
                "group_features": {
                    "hybrid_zscore": -0.2 if top_is_oracle else 1.2,
                    "semantic_rerank_zscore": -0.1 if top_is_oracle else 1.3,
                    "spec_rerank_zscore": 0.1 if top_is_oracle else 0.9,
                    "query_token_in_candidate_ratio": 0.4 if top_is_oracle else 0.8,
                    "candidate_token_in_query_ratio": 0.5 if top_is_oracle else 0.8,
                    "canonical_term_coverage": 0.7 if top_is_oracle else 0.6,
                    "core_term_bigram_jaccard": 0.6 if top_is_oracle else 0.7,
                    "hybrid_rank": 2 if top_is_oracle else 1,
                    "rrf_rank": 2 if top_is_oracle else 1,
                    "candidate_specificity_score": 0.8 if top_is_oracle else 0.4,
                    "family_confidence": 1.0,
                    "entity_confidence": 1.0,
                    "material_confidence": 1.0,
                    "install_method_confidence": 1.0,
                    "connection_confidence": 0.4,
                    "system_confidence": 0.8,
                    "family_match": 1,
                    "entity_match": 1,
                    "material_match": 1,
                    "install_method_match": 1,
                    "system_match": 1,
                    "dn_is_upward_nearest": 1 if top_is_oracle else 0,
                },
            },
            {
                "quota_id": "Q2" if top_is_oracle else "Q1",
                "is_oracle": 0 if top_is_oracle else 1,
                "cgr_feasible": True,
                "cgr_prior_score": 0.01,
                "cgr_tier_penalty": 0.4 if top_is_oracle else 0.0,
                "cgr_generic_penalty": 0.2,
                "cgr_soft_conflict_penalty": 0.2 if top_is_oracle else 0.0,
                "cgr_sem_score": 0.8 if top_is_oracle else 0.4,
                "cgr_str_score": 0.2 if top_is_oracle else 0.9,
                "rerank_score": 0.92 if top_is_oracle else 0.45,
                "param_score": 0.42 if top_is_oracle else 0.96,
                "logic_score": 0.40 if top_is_oracle else 0.94,
                "feature_alignment_score": 0.70 if top_is_oracle else 0.92,
                "context_alignment_score": 0.65 if top_is_oracle else 0.90,
                "_ltr_param": {
                    "param_main_rel_dist": 0.5 if top_is_oracle else 0.0,
                    "param_main_exact": 0 if top_is_oracle else 1,
                    "param_material_match": 1.0,
                },
                "group_features": {
                    "hybrid_zscore": 1.2 if top_is_oracle else -0.2,
                    "semantic_rerank_zscore": 1.3 if top_is_oracle else -0.1,
                    "spec_rerank_zscore": 0.9 if top_is_oracle else 0.1,
                    "query_token_in_candidate_ratio": 0.8 if top_is_oracle else 0.4,
                    "candidate_token_in_query_ratio": 0.8 if top_is_oracle else 0.5,
                    "canonical_term_coverage": 0.6 if top_is_oracle else 0.7,
                    "core_term_bigram_jaccard": 0.7 if top_is_oracle else 0.6,
                    "hybrid_rank": 1 if top_is_oracle else 2,
                    "rrf_rank": 1 if top_is_oracle else 2,
                    "candidate_specificity_score": 0.4 if top_is_oracle else 0.8,
                    "family_confidence": 1.0,
                    "entity_confidence": 1.0,
                    "material_confidence": 1.0,
                    "install_method_confidence": 1.0,
                    "connection_confidence": 0.4,
                    "system_confidence": 0.8,
                    "family_match": 1,
                    "entity_match": 1,
                    "material_match": 1,
                    "install_method_match": 1,
                    "system_match": 1,
                    "dn_is_upward_nearest": 0 if top_is_oracle else 1,
                },
            },
        ],
    }


def _accept_row(sample_id: str, split: str, accept_label: int) -> dict:
    return {
        "sample_id": sample_id,
        "split": split,
        "accept_label": accept_label,
        "p1": 0.82 if accept_label else 0.38,
        "p1_minus_p2": 0.30 if accept_label else 0.05,
        "p1_minus_p3": 0.35 if accept_label else 0.08,
        "candidate_count": 2,
        "ambiguity": 0.2 if accept_label else 0.7,
        "hard_conflict_top1": 0,
        "tier_penalty_top1": 0.05 if accept_label else 0.55,
        "generic_penalty_top1": 0.10 if accept_label else 0.35,
        "query_param_coverage": 0.8 if accept_label else 0.1,
        "family_confidence": 0.9 if accept_label else 0.3,
        "has_material": 1,
        "has_install_method": 1,
        "route": "installation_spec" if accept_label else "ambiguous_short",
    }


def test_train_cgr_model_produces_sections_and_metrics():
    group_rows = [
        _group_row("g1", "train", True),
        _group_row("g2", "train", True),
        _group_row("g3", "val", True),
    ]
    accept_rows = [
        _accept_row("a1", "train", 1),
        _accept_row("a2", "train", 0),
        _accept_row("a3", "val", 1),
        _accept_row("a4", "val", 0),
    ]

    result = train_cgr_model(group_rows, accept_rows, epochs=80, lr=0.1)

    assert result["model"]["semantic_expert"]["feature_names"]
    assert result["model"]["structural_expert"]["feature_names"]
    assert result["model"]["gate"]["feature_names"]
    assert result["model"]["accept_head"]["feature_names"]
    assert result["manifest"]["counts"]["group_rows"] == 3
    assert result["manifest"]["metrics"]["ranking_train_acc"] >= 0.5


def test_train_cgr_model_from_files_writes_model():
    temp_root = Path("output/_tmp_cgr_train_test")
    shutil.rmtree(temp_root, ignore_errors=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        group_path = temp_root / "cgr_group_train.jsonl"
        accept_path = temp_root / "cgr_accept_train.jsonl"
        output_path = temp_root / "cgr_model.json"

        group_rows = [
            _group_row("g1", "train", True),
            _group_row("g2", "val", True),
        ]
        accept_rows = [
            _accept_row("a1", "train", 1),
            _accept_row("a2", "train", 0),
            _accept_row("a3", "val", 1),
        ]

        group_path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in group_rows) + "\n",
            encoding="utf-8",
        )
        accept_path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in accept_rows) + "\n",
            encoding="utf-8",
        )

        result = train_cgr_model_from_files(group_path, accept_path, output_path, epochs=50, lr=0.1)

        assert output_path.exists()
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        assert payload["semantic_expert"]["feature_names"]
        assert result["manifest"]["files"]["model_path"] == str(output_path)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
