from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

import config
from src.candidate_scoring import compute_candidate_structured_score
from src.constrained_ranker import apply_constrained_gated_ranker
from src.ltr_feature_extractor import extract_group_features


class LTRRanker:
    _model = None
    _feature_names: list[str] | None = None
    _load_attempted = False
    _load_error = ""

    @classmethod
    def _load(cls) -> tuple[object | None, list[str]]:
        if cls._load_attempted:
            return cls._model, list(cls._feature_names or [])
        cls._load_attempted = True
        model_path = Path(config.LTR_V2_MODEL_PATH)
        feature_path = Path(config.LTR_V2_FEATURES_PATH)
        if not model_path.exists():
            cls._load_error = f"model_missing:{model_path}"
            return None, []
        try:
            import lightgbm as lgb

            cls._model = lgb.Booster(model_file=str(model_path))
            feature_names: list[str] = []
            if feature_path.exists():
                payload = json.loads(feature_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    feature_names = [str(name) for name in payload.get("feature_names") or [] if str(name)]
                elif isinstance(payload, list):
                    feature_names = [str(name) for name in payload if str(name)]
            if not feature_names and cls._model is not None:
                feature_names = [str(name) for name in cls._model.feature_name() if str(name)]
            cls._feature_names = feature_names
            cls._load_error = ""
        except Exception as exc:
            cls._model = None
            cls._feature_names = []
            cls._load_error = f"load_failed:{exc}"
            logger.warning(f"LTR v2 model load failed, fallback to manual scoring: {exc}")
        return cls._model, list(cls._feature_names or [])

    @classmethod
    def rerank_candidates_with_ltr(
        cls,
        item: dict,
        candidates: list[dict],
        context: dict | None = None,
    ) -> tuple[list[dict], dict]:
        context = context or {}
        meta = {
            "enabled": bool(config.LTR_V2_ENABLED),
            "applied": False,
            "fallback_reason": "",
            "pre_ltr_top1_id": str((candidates or [{}])[0].get("quota_id", "") or "") if candidates else "",
            "post_ltr_top1_id": str((candidates or [{}])[0].get("quota_id", "") or "") if candidates else "",
            "post_cgr_top1_id": str((candidates or [{}])[0].get("quota_id", "") or "") if candidates else "",
            "feature_count": 0,
            "cgr": {},
        }
        if not candidates:
            meta["fallback_reason"] = "no_candidates"
            return candidates, meta
        for candidate in candidates:
            candidate["manual_structured_score"] = compute_candidate_structured_score(candidate)
            candidate["_rank_score_source"] = "manual"
        ranked = list(candidates)
        if not config.LTR_V2_ENABLED:
            meta["fallback_reason"] = "disabled"
            if config.CONSTRAINED_GATED_RANKER_ENABLED:
                ranked, cgr_meta = apply_constrained_gated_ranker(item, ranked, context)
                meta["cgr"] = cgr_meta
                meta["post_cgr_top1_id"] = str((ranked[0].get("quota_id", "") if ranked else "") or "")
            return ranked, meta

        model, feature_names = cls._load()
        if model is None or not feature_names:
            meta["fallback_reason"] = cls._load_error or "model_unavailable"
            if config.CONSTRAINED_GATED_RANKER_ENABLED:
                ranked, cgr_meta = apply_constrained_gated_ranker(item, ranked, context)
                meta["cgr"] = cgr_meta
                meta["post_cgr_top1_id"] = str((ranked[0].get("quota_id", "") if ranked else "") or "")
            return ranked, meta

        try:
            feature_rows = extract_group_features(item, candidates, context)
            meta["feature_count"] = len(feature_names)
            matrix = []
            for candidate, feature_row in zip(candidates, feature_rows):
                candidate["ltr_feature_snapshot"] = feature_row
                matrix.append([float(feature_row.get(name, 0.0)) for name in feature_names])
            predictions = model.predict(matrix)
            ranked = list(candidates)
            for candidate, score in zip(ranked, predictions):
                candidate["ltr_score"] = float(score)
                candidate["_rank_score_source"] = "ltr"
            ranked.sort(
                key=lambda candidate: (
                    float(candidate.get("ltr_score", 0.0)),
                    float(candidate.get("manual_structured_score", 0.0)),
                    float(candidate.get("hybrid_score", candidate.get("rerank_score", 0.0)) or 0.0),
                ),
                reverse=True,
            )
            meta["applied"] = True
            meta["post_ltr_top1_id"] = str(ranked[0].get("quota_id", "") or "") if ranked else ""
            if config.CONSTRAINED_GATED_RANKER_ENABLED:
                ranked, cgr_meta = apply_constrained_gated_ranker(item, ranked, context)
                meta["cgr"] = cgr_meta
                meta["post_cgr_top1_id"] = str((ranked[0].get("quota_id", "") if ranked else "") or "")
            if config.LTR_FEATURE_LOGGING:
                top_k = max(int(config.LTR_FEATURE_LOG_TOPK), 1)
                preview = []
                for candidate in ranked[:top_k]:
                    preview.append({
                        "quota_id": candidate.get("quota_id", ""),
                        "name": str(candidate.get("name", ""))[:40],
                        "ltr_score": round(float(candidate.get("ltr_score", 0.0)), 6),
                        "manual_structured_score": round(float(candidate.get("manual_structured_score", 0.0)), 6),
                    })
                logger.info(f"LTR rerank preview: {preview}")
            return ranked, meta
        except Exception as exc:
            meta["fallback_reason"] = f"predict_failed:{exc}"
            logger.warning(f"LTR rerank failed, fallback to manual scoring: {exc}")
            if config.CONSTRAINED_GATED_RANKER_ENABLED:
                ranked, cgr_meta = apply_constrained_gated_ranker(item, ranked, context)
                meta["cgr"] = cgr_meta
                meta["post_cgr_top1_id"] = str((ranked[0].get("quota_id", "") if ranked else "") or "")
            return ranked, meta


def rerank_candidates_with_ltr(
    item: dict,
    candidates: list[dict],
    context: dict | None = None,
) -> tuple[list[dict], dict]:
    return LTRRanker.rerank_candidates_with_ltr(item, candidates, context)


__all__ = ["LTRRanker", "rerank_candidates_with_ltr"]
