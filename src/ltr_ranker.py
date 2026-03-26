from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

import config
from src.candidate_scoring import (
    compute_candidate_stage_rank_key,
    compute_candidate_structured_score,
)
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

    @staticmethod
    def _annotate_manual_stage(candidates: list[dict]) -> None:
        for candidate in candidates:
            candidate["manual_structured_score"] = compute_candidate_structured_score(candidate)
            candidate["_rank_score_source"] = "manual"

    @staticmethod
    def _sort_with_stage_priority(
        candidates: list[dict],
        *,
        stage: str,
        primary_score_field: str,
    ) -> list[dict]:
        ranked = list(candidates)
        ranked.sort(
            key=lambda candidate: compute_candidate_stage_rank_key(
                candidate,
                primary_score=float(candidate.get(primary_score_field, 0.0) or 0.0),
            ),
            reverse=True,
        )
        for candidate in ranked:
            candidate["rank_stage"] = stage
            candidate["rank_score"] = float(candidate.get(primary_score_field, 0.0) or 0.0)
        return ranked

    @staticmethod
    def _find_candidate_by_quota_id(candidates: list[dict], quota_id: str) -> dict | None:
        target = str(quota_id or "").strip()
        if not target:
            return None
        for candidate in candidates:
            if str(candidate.get("quota_id", "") or "").strip() == target:
                return candidate
        return None

    @staticmethod
    def _should_allow_cgr_override(incumbent: dict | None, challenger: dict | None, cgr_meta: dict) -> tuple[bool, str]:
        if challenger is None:
            return False, "missing_challenger"
        if incumbent is None:
            return False, "missing_incumbent"
        incumbent_id = str(incumbent.get("quota_id", "") or "").strip()
        challenger_id = str(challenger.get("quota_id", "") or "").strip()
        if incumbent_id == challenger_id:
            return True, "same_top1"
        if bool(cgr_meta.get("empty_feasible_set")):
            return False, "empty_feasible_set"
        if not bool(challenger.get("cgr_feasible", True)):
            return False, "challenger_not_feasible"
        if bool(incumbent.get("cgr_fatal_hard_conflict")):
            return True, "incumbent_fatal_hard_conflict"
        if bool(incumbent.get("cgr_high_conf_wrong_book")):
            return True, "incumbent_high_conf_wrong_book"
        return False, "incumbent_protected"

    @classmethod
    def _apply_cgr_shadow_guard(
        cls,
        ltr_ranked: list[dict],
        cgr_ranked: list[dict],
        cgr_meta: dict,
    ) -> tuple[list[dict], dict]:
        if not cgr_ranked:
            cgr_meta["override_allowed"] = False
            cgr_meta["override_reason"] = "empty_cgr_ranked"
            return ltr_ranked, cgr_meta
        incumbent_id = str((ltr_ranked[0].get("quota_id", "") if ltr_ranked else "") or "")
        challenger_id = str((cgr_ranked[0].get("quota_id", "") if cgr_ranked else "") or "")
        cgr_meta["suggested_top1_id"] = challenger_id
        incumbent = cls._find_candidate_by_quota_id(cgr_ranked, incumbent_id)
        challenger = cgr_ranked[0]
        allow_override, reason = cls._should_allow_cgr_override(incumbent, challenger, cgr_meta)
        cgr_meta["override_allowed"] = allow_override
        cgr_meta["override_reason"] = reason
        cgr_meta["incumbent_top1_id"] = incumbent_id
        if allow_override:
            return cgr_ranked, cgr_meta
        guarded = list(cgr_ranked)
        if incumbent is not None:
            guarded = [incumbent] + [candidate for candidate in guarded if candidate is not incumbent]
        else:
            guarded = list(ltr_ranked)
        return guarded, cgr_meta

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
            "post_manual_top1_id": str((candidates or [{}])[0].get("quota_id", "") or "") if candidates else "",
            "post_ltr_top1_id": str((candidates or [{}])[0].get("quota_id", "") or "") if candidates else "",
            "post_cgr_top1_id": str((candidates or [{}])[0].get("quota_id", "") or "") if candidates else "",
            "feature_count": 0,
            "cgr": {},
            "primary_stage": "manual",
        }
        if not candidates:
            meta["fallback_reason"] = "no_candidates"
            return candidates, meta
        cls._annotate_manual_stage(candidates)
        ranked = cls._sort_with_stage_priority(
            candidates,
            stage="manual",
            primary_score_field="manual_structured_score",
        )
        meta["post_manual_top1_id"] = str(ranked[0].get("quota_id", "") or "") if ranked else ""
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
            ranked = cls._sort_with_stage_priority(
                ranked,
                stage="ltr",
                primary_score_field="ltr_score",
            )
            meta["applied"] = True
            meta["primary_stage"] = "ltr"
            meta["post_ltr_top1_id"] = str(ranked[0].get("quota_id", "") or "") if ranked else ""
            if config.CONSTRAINED_GATED_RANKER_ENABLED:
                cgr_ranked, cgr_meta = apply_constrained_gated_ranker(item, ranked, context)
                ranked, cgr_meta = cls._apply_cgr_shadow_guard(ranked, cgr_ranked, cgr_meta)
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
