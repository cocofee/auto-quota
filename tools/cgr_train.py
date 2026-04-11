from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

import config
from src.constrained_ranker import (
    ACCEPT_MODEL_FEATURES,
    GATE_MODEL_FEATURES,
    SEMANTIC_MODEL_FEATURES,
    STRUCTURAL_MODEL_FEATURES,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GROUP_PATH = PROJECT_ROOT / "output" / "benchmark_training" / "cgr_group_train.jsonl"
DEFAULT_ACCEPT_PATH = PROJECT_ROOT / "output" / "benchmark_training" / "cgr_accept_train.jsonl"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "cgr_model.json"


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _sigmoid_array(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _group_minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    low = min(values)
    high = max(values)
    if abs(high - low) <= 1e-9:
        return [0.5 for _ in values]
    return [(value - low) / (high - low) for value in values]


def _candidate_semantic_features(candidate: dict) -> dict[str, float]:
    group = dict(candidate.get("group_features") or {})
    return {
        "hybrid_zscore": float(group.get("hybrid_zscore", 0.0) or 0.0),
        "rerank_score": float(candidate.get("rerank_score", 0.0) or 0.0),
        "semantic_rerank_zscore": float(group.get("semantic_rerank_zscore", 0.0) or 0.0),
        "spec_rerank_zscore": float(group.get("spec_rerank_zscore", 0.0) or 0.0),
        "name_bonus": float(candidate.get("name_bonus", 0.0) or 0.0),
        "query_token_in_candidate_ratio": float(group.get("query_token_in_candidate_ratio", 0.0) or 0.0),
        "candidate_token_in_query_ratio": float(group.get("candidate_token_in_query_ratio", 0.0) or 0.0),
        "canonical_term_coverage": float(group.get("canonical_term_coverage", 0.0) or 0.0),
        "core_term_bigram_jaccard": float(group.get("core_term_bigram_jaccard", 0.0) or 0.0),
        "inverse_hybrid_rank": 1.0 / max(int(group.get("hybrid_rank", 1) or 1), 1),
        "inverse_rrf_rank": 1.0 / max(int(group.get("rrf_rank", 1) or 1), 1),
    }


def _candidate_structural_features(candidate: dict) -> dict[str, float]:
    group = dict(candidate.get("group_features") or {})
    ltr_param = dict(candidate.get("_ltr_param") or {})
    return {
        "param_score": float(candidate.get("param_score", 0.0) or 0.0),
        "logic_score": float(candidate.get("logic_score", 0.0) or 0.0),
        "feature_alignment_score": float(candidate.get("feature_alignment_score", 0.0) or 0.0),
        "context_alignment_score": float(candidate.get("context_alignment_score", 0.0) or 0.0),
        "param_main_rel_score": max(0.0, 1.0 - float(ltr_param.get("param_main_rel_dist", 1.0) or 1.0)),
        "param_main_exact": float(ltr_param.get("param_main_exact", 0.0) or 0.0),
        "param_material_match": float(ltr_param.get("param_material_match", 0.0) or 0.0),
        "candidate_specificity_score": float(group.get("candidate_specificity_score", 0.0) or 0.0),
        "structural_anchor_confidence": float(np.mean([
            float(group.get("family_confidence", 0.0) or 0.0),
            float(group.get("entity_confidence", 0.0) or 0.0),
            float(group.get("material_confidence", 0.0) or 0.0),
            float(group.get("install_method_confidence", 0.0) or 0.0),
            float(group.get("connection_confidence", 0.0) or 0.0),
            float(group.get("system_confidence", 0.0) or 0.0),
        ])),
        "upward_nearest": max(
            [float(value or 0.0) for key, value in group.items() if str(key).endswith("_is_upward_nearest")] or [0.0]
        ),
        "family_match": float(group.get("family_match", 0.0) or 0.0),
        "entity_match": float(group.get("entity_match", 0.0) or 0.0),
        "material_match": float(group.get("material_match", 0.0) or 0.0),
        "install_method_match": float(group.get("install_method_match", 0.0) or 0.0),
        "system_match": float(group.get("system_match", 0.0) or 0.0),
    }


def _gate_features(group_row: dict) -> dict[str, float]:
    summary = dict(group_row.get("query_summary") or {})
    route = str(summary.get("route") or "")
    return {
        "family_confidence": float(summary.get("family_confidence", 0.0) or 0.0),
        "query_param_coverage": float(summary.get("query_param_coverage", 0.0) or 0.0),
        "group_ambiguity_score": float(summary.get("group_ambiguity_score", 0.5) or 0.5),
        "candidate_count": float(summary.get("candidate_count", 0) or 0),
        "has_material": float(summary.get("has_material", 0) or 0),
        "has_install_method": float(summary.get("has_install_method", 0) or 0),
        "route_installation_spec": float(route == "installation_spec"),
        "route_material": float(route == "material"),
        "route_semantic_description": float(route == "semantic_description"),
        "route_ambiguous_short": float(route == "ambiguous_short"),
    }


def _accept_features(row: dict) -> dict[str, float]:
    route = str(row.get("route") or "")
    return {
        "p1": float(row.get("p1", 0.0) or 0.0),
        "p1_minus_p2": float(row.get("p1_minus_p2", 0.0) or 0.0),
        "p1_minus_p3": float(row.get("p1_minus_p3", 0.0) or 0.0),
        "candidate_count": float(row.get("candidate_count", 0) or 0),
        "ambiguity": float(row.get("ambiguity", 0.5) or 0.5),
        "hard_conflict_top1": float(row.get("hard_conflict_top1", 0) or 0),
        "tier_penalty_top1": float(row.get("tier_penalty_top1", 0.0) or 0.0),
        "generic_penalty_top1": float(row.get("generic_penalty_top1", 0.0) or 0.0),
        "query_param_coverage": float(row.get("query_param_coverage", 0.0) or 0.0),
        "family_confidence": float(row.get("family_confidence", 0.0) or 0.0),
        "has_material": float(row.get("has_material", 0) or 0),
        "has_install_method": float(row.get("has_install_method", 0) or 0),
        "route_installation_spec": float(route == "installation_spec"),
        "route_material": float(route == "material"),
        "route_semantic_description": float(route == "semantic_description"),
        "route_ambiguous_short": float(route == "ambiguous_short"),
    }


def _vectorize(feature_dicts: list[dict[str, float]], feature_names: tuple[str, ...]) -> np.ndarray:
    if not feature_dicts:
        return np.zeros((0, len(feature_names)), dtype=float)
    return np.array(
        [[float(features.get(name, 0.0) or 0.0) for name in feature_names] for features in feature_dicts],
        dtype=float,
    )


def _fit_binary_logistic(
    X: np.ndarray,
    y: np.ndarray,
    sample_weight: np.ndarray | None = None,
    *,
    epochs: int = 300,
    lr: float = 0.05,
    l2: float = 1e-4,
) -> tuple[np.ndarray, float]:
    if X.size == 0:
        return np.zeros((X.shape[1] if X.ndim == 2 else 0,), dtype=float), 0.0
    n_features = X.shape[1]
    weights = np.zeros(n_features, dtype=float)
    bias = 0.0
    sample_weight = sample_weight if sample_weight is not None else np.ones(len(X), dtype=float)
    sample_weight = sample_weight.reshape(-1, 1)
    y = y.reshape(-1, 1)
    for _ in range(max(epochs, 1)):
        logits = X @ weights.reshape(-1, 1) + bias
        preds = _sigmoid_array(logits)
        error = (preds - y) * sample_weight
        grad_w = (X * error).mean(axis=0) + l2 * weights
        grad_b = float(error.mean())
        weights -= lr * grad_w
        bias -= lr * grad_b
    return weights, bias


def _fit_pairwise_expert(
    group_rows: list[dict],
    feature_names: tuple[str, ...],
    feature_builder,
    *,
    epochs: int = 300,
    lr: float = 0.05,
    l2: float = 1e-4,
) -> dict:
    candidate_feature_dicts: list[dict[str, float]] = []
    diff_rows: list[np.ndarray] = []
    diff_weights: list[float] = []

    for group in group_rows:
        candidates = list(group.get("candidates") or [])
        positives = [candidate for candidate in candidates if int(candidate.get("is_oracle", 0)) == 1]
        negatives = [candidate for candidate in candidates if int(candidate.get("is_oracle", 0)) == 0]
        if not positives or not negatives:
            continue
        positive = max(positives, key=lambda row: float(row.get("cgr_score", 0.0) or 0.0))
        positive_features = feature_builder(positive)
        candidate_feature_dicts.append(positive_features)
        for negative in negatives:
            negative_features = feature_builder(negative)
            candidate_feature_dicts.append(negative_features)
            diff = np.array(
                [
                    float(positive_features.get(name, 0.0) or 0.0) - float(negative_features.get(name, 0.0) or 0.0)
                    for name in feature_names
                ],
                dtype=float,
            )
            diff_rows.append(diff)
            same_family = float(negative.get("group_features", {}).get("family_match", 0.0) or 0.0)
            same_entity = float(negative.get("group_features", {}).get("entity_match", 0.0) or 0.0)
            hard_weight = 1.0 + 0.5 * same_family + 0.25 * same_entity
            diff_weights.append(hard_weight)

    candidate_matrix = _vectorize(candidate_feature_dicts, feature_names)
    scales = candidate_matrix.std(axis=0) if len(candidate_matrix) else np.ones(len(feature_names), dtype=float)
    scales = np.where(scales <= 1e-6, 1.0, scales)
    if not diff_rows:
        return {
            "feature_names": list(feature_names),
            "weights": [0.0 for _ in feature_names],
            "bias": 0.0,
            "means": [0.0 for _ in feature_names],
            "scales": [float(value) for value in scales],
            "pair_examples": 0,
        }

    X = np.vstack(diff_rows) / scales
    y = np.ones(len(X), dtype=float)
    weights, bias = _fit_binary_logistic(
        X,
        y,
        np.array(diff_weights, dtype=float),
        epochs=epochs,
        lr=lr,
        l2=l2,
    )
    return {
        "feature_names": list(feature_names),
        "weights": [float(value) for value in weights],
        "bias": float(bias),
        "means": [0.0 for _ in feature_names],
        "scales": [float(value) for value in scales],
        "pair_examples": int(len(X)),
    }


def _fit_classifier(
    feature_dicts: list[dict[str, float]],
    labels: list[int],
    feature_names: tuple[str, ...],
    *,
    epochs: int = 300,
    lr: float = 0.05,
    l2: float = 1e-4,
) -> dict:
    X = _vectorize(feature_dicts, feature_names)
    if len(X) == 0:
        return {
            "feature_names": list(feature_names),
            "weights": [0.0 for _ in feature_names],
            "bias": 0.0,
            "means": [0.0 for _ in feature_names],
            "scales": [1.0 for _ in feature_names],
            "examples": 0,
        }
    means = X.mean(axis=0)
    scales = X.std(axis=0)
    scales = np.where(scales <= 1e-6, 1.0, scales)
    X_std = (X - means) / scales
    y = np.array(labels, dtype=float)
    weights, bias = _fit_binary_logistic(X_std, y, epochs=epochs, lr=lr, l2=l2)
    return {
        "feature_names": list(feature_names),
        "weights": [float(value) for value in weights],
        "bias": float(bias),
        "means": [float(value) for value in means],
        "scales": [float(value) for value in scales],
        "examples": int(len(X)),
    }


def _apply_linear_model(features: dict[str, float], section: dict) -> float:
    names = list(section.get("feature_names") or [])
    weights = list(section.get("weights") or [])
    means = list(section.get("means") or [])
    scales = list(section.get("scales") or [])
    bias = float(section.get("bias", 0.0) or 0.0)
    total = bias
    for index, name in enumerate(names):
        value = float(features.get(name, 0.0) or 0.0)
        mean = float(means[index] if index < len(means) else 0.0)
        scale = float(scales[index] if index < len(scales) else 1.0)
        weight = float(weights[index] if index < len(weights) else 0.0)
        total += ((value - mean) / (scale if abs(scale) > 1e-9 else 1.0)) * weight
    return total


def _gate_target(group_row: dict) -> int:
    candidates = list(group_row.get("candidates") or [])
    oracle_ids = {str(value).strip() for value in group_row.get("oracle_quota_ids") or [] if str(value).strip()}
    if not candidates or not oracle_ids:
        return 0
    sem_rank = None
    str_rank = None
    for rank, candidate in enumerate(sorted(candidates, key=lambda row: float(row.get("cgr_sem_score", 0.0) or 0.0), reverse=True), start=1):
        if str(candidate.get("quota_id", "") or "") in oracle_ids:
            sem_rank = rank
            break
    for rank, candidate in enumerate(sorted(candidates, key=lambda row: float(row.get("cgr_str_score", 0.0) or 0.0), reverse=True), start=1):
        if str(candidate.get("quota_id", "") or "") in oracle_ids:
            str_rank = rank
            break
    sem_rank = sem_rank or len(candidates) + 1
    str_rank = str_rank or len(candidates) + 1
    return int(sem_rank <= str_rank)


def _score_group(group_row: dict, model: dict) -> tuple[str, bool]:
    candidates = list(group_row.get("candidates") or [])
    if not candidates:
        return "", False
    gate = float(_sigmoid_array(np.array([_apply_linear_model(_gate_features(group_row), model["gate"])]))[0])
    sem_raw = [_apply_linear_model(_candidate_semantic_features(candidate), model["semantic_expert"]) for candidate in candidates]
    str_raw = [_apply_linear_model(_candidate_structural_features(candidate), model["structural_expert"]) for candidate in candidates]
    sem_scores = _group_minmax(sem_raw)
    str_scores = _group_minmax(str_raw)
    scored: list[tuple[str, float]] = []
    oracle_ids = {str(value).strip() for value in group_row.get("oracle_quota_ids") or [] if str(value).strip()}
    for candidate, sem_score, str_score in zip(candidates, sem_scores, str_scores):
        score = (
            gate * sem_score
            + (1.0 - gate) * str_score
            + float(candidate.get("cgr_prior_score", 0.0) or 0.0)
            - 0.45 * float(candidate.get("cgr_tier_penalty", 0.0) or 0.0)
            - 0.20 * float(candidate.get("cgr_generic_penalty", 0.0) or 0.0)
            - 0.25 * float(candidate.get("cgr_soft_conflict_penalty", 0.0) or 0.0)
        )
        if not bool(candidate.get("cgr_feasible", True)):
            score = -1e9
        scored.append((str(candidate.get("quota_id", "") or ""), score))
    top_quota_id = max(scored, key=lambda pair: pair[1])[0] if scored else ""
    return top_quota_id, bool(top_quota_id and top_quota_id in oracle_ids)


def _accuracy_on_groups(group_rows: list[dict], model: dict) -> float:
    if not group_rows:
        return 0.0
    correct = 0
    for group_row in group_rows:
        _, hit = _score_group(group_row, model)
        correct += int(hit)
    return correct / max(len(group_rows), 1)


def _accuracy_on_classifier(rows: list[dict], feature_builder, model_section: dict) -> float:
    if not rows:
        return 0.0
    correct = 0
    for row in rows:
        score = _apply_linear_model(feature_builder(row), model_section)
        pred = int(float(_sigmoid_array(np.array([score]))[0]) >= 0.5)
        label = int(row.get("accept_label", row.get("_label", 0)) or 0)
        correct += int(pred == label)
    return correct / max(len(rows), 1)


def train_cgr_model(
    group_rows: list[dict],
    accept_rows: list[dict],
    *,
    epochs: int = 300,
    lr: float = 0.05,
    l2: float = 1e-4,
    temperature: float | None = None,
) -> dict:
    train_groups = [row for row in group_rows if str(row.get("split") or "train") == "train"]
    val_groups = [row for row in group_rows if str(row.get("split") or "") == "val"]
    if not train_groups:
        train_groups = list(group_rows)
    if not val_groups:
        val_groups = list(train_groups)

    train_accept = [row for row in accept_rows if str(row.get("split") or "train") == "train"]
    val_accept = [row for row in accept_rows if str(row.get("split") or "") == "val"]
    if not train_accept:
        train_accept = list(accept_rows)
    if not val_accept:
        val_accept = list(train_accept)

    semantic_expert = _fit_pairwise_expert(
        train_groups,
        SEMANTIC_MODEL_FEATURES,
        _candidate_semantic_features,
        epochs=epochs,
        lr=lr,
        l2=l2,
    )
    structural_expert = _fit_pairwise_expert(
        train_groups,
        STRUCTURAL_MODEL_FEATURES,
        _candidate_structural_features,
        epochs=epochs,
        lr=lr,
        l2=l2,
    )

    gate_feature_dicts = [_gate_features(row) for row in train_groups]
    gate_labels = [_gate_target(row) for row in train_groups]
    gate_train_rows = []
    for row, label in zip(train_groups, gate_labels):
        copy_row = dict(row)
        copy_row["_label"] = label
        gate_train_rows.append(copy_row)
    gate = _fit_classifier(
        gate_feature_dicts,
        gate_labels,
        GATE_MODEL_FEATURES,
        epochs=epochs,
        lr=lr,
        l2=l2,
    )

    accept = _fit_classifier(
        [_accept_features(row) for row in train_accept],
        [int(row.get("accept_label", 0) or 0) for row in train_accept],
        ACCEPT_MODEL_FEATURES,
        epochs=epochs,
        lr=lr,
        l2=l2,
    )

    val_candidates = [float(row.get("p1", 0.0) or 0.0) for row in val_accept] or [config.CGR_MIN_TOP1_PROB]
    best_threshold = config.CGR_ACCEPT_THRESHOLD
    best_min_prob = config.CGR_MIN_TOP1_PROB
    best_objective = -1.0
    for accept_threshold in np.linspace(0.45, 0.80, 15):
        for min_prob in [0.35, 0.45, 0.55, 0.65]:
            accepted = 0
            true_positive = 0
            for row in val_accept:
                score = float(_sigmoid_array(np.array([_apply_linear_model(_accept_features(row), accept)]))[0])
                p1 = float(row.get("p1", 0.0) or 0.0)
                label = int(row.get("accept_label", 0) or 0)
                if score >= accept_threshold and p1 >= min_prob:
                    accepted += 1
                    true_positive += label
            precision = true_positive / max(accepted, 1)
            coverage = accepted / max(len(val_accept), 1)
            objective = precision * coverage
            if objective > best_objective:
                best_objective = objective
                best_threshold = float(accept_threshold)
                best_min_prob = float(min_prob)

    model = {
        "version": "cgr_v1_linear",
        "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "temperature": float(temperature if temperature is not None else config.CGR_TEMPERATURE),
        "accept_threshold": best_threshold,
        "min_top1_prob": best_min_prob,
        "semantic_expert": semantic_expert,
        "structural_expert": structural_expert,
        "gate": gate,
        "accept_head": accept,
    }

    gate_val_rows = []
    for row in val_groups:
        copy_row = dict(row)
        copy_row["_label"] = _gate_target(row)
        gate_val_rows.append(copy_row)

    manifest = {
        "counts": {
            "group_rows": len(group_rows),
            "accept_rows": len(accept_rows),
            "train_groups": len(train_groups),
            "val_groups": len(val_groups),
            "train_accept": len(train_accept),
            "val_accept": len(val_accept),
        },
        "pair_examples": {
            "semantic": semantic_expert.get("pair_examples", 0),
            "structural": structural_expert.get("pair_examples", 0),
        },
        "metrics": {
            "ranking_train_acc": round(_accuracy_on_groups(train_groups, model), 4),
            "ranking_val_acc": round(_accuracy_on_groups(val_groups, model), 4),
            "gate_train_acc": round(_accuracy_on_classifier(gate_train_rows, _gate_features, gate), 4),
            "gate_val_acc": round(_accuracy_on_classifier(gate_val_rows, _gate_features, gate), 4),
            "accept_train_acc": round(_accuracy_on_classifier(train_accept, _accept_features, accept), 4),
            "accept_val_acc": round(_accuracy_on_classifier(val_accept, _accept_features, accept), 4),
        },
        "split_counts": dict(Counter(str(row.get("split") or "train") for row in group_rows)),
    }
    return {
        "model": model,
        "manifest": manifest,
    }


def train_cgr_model_from_files(
    group_path: str | Path,
    accept_path: str | Path,
    output_path: str | Path,
    *,
    epochs: int = 300,
    lr: float = 0.05,
    l2: float = 1e-4,
    temperature: float | None = None,
) -> dict:
    group_rows = _read_jsonl(Path(group_path))
    accept_rows = _read_jsonl(Path(accept_path))
    result = train_cgr_model(
        group_rows,
        accept_rows,
        epochs=epochs,
        lr=lr,
        l2=l2,
        temperature=temperature,
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result["model"], ensure_ascii=False, indent=2), encoding="utf-8")
    result["manifest"]["files"] = {
        "group_path": str(Path(group_path)),
        "accept_path": str(Path(accept_path)),
        "model_path": str(output_path),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Train constrained gated ranker linear V1")
    parser.add_argument("--group-data", default=str(DEFAULT_GROUP_PATH), help="cgr group jsonl path")
    parser.add_argument("--accept-data", default=str(DEFAULT_ACCEPT_PATH), help="cgr accept jsonl path")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="output cgr model json")
    parser.add_argument("--epochs", type=int, default=300, help="gradient descent epochs")
    parser.add_argument("--lr", type=float, default=0.05, help="learning rate")
    parser.add_argument("--l2", type=float, default=1e-4, help="L2 regularization")
    parser.add_argument("--temperature", type=float, default=config.CGR_TEMPERATURE, help="softmax temperature")
    args = parser.parse_args()

    result = train_cgr_model_from_files(
        args.group_data,
        args.accept_data,
        args.output,
        epochs=args.epochs,
        lr=args.lr,
        l2=args.l2,
        temperature=args.temperature,
    )
    print(f"[OK] wrote cgr model: {args.output}")
    print(f"[COUNT] {result['manifest']['counts']}")
    print(f"[PAIR] {result['manifest']['pair_examples']}")
    print(f"[METRIC] {result['manifest']['metrics']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
