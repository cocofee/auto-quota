from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

import config
from src.candidate_canonicalizer import build_candidate_canonical_features
from src.candidate_scoring import (
    compute_candidate_stage_rank_key,
    compute_candidate_structured_score,
    sort_candidates_with_stage_priority,
)
from src.constrained_ranker import apply_constrained_gated_ranker
from src.ltr_feature_extractor import extract_group_features
from src.ltr_model_cache import LTRModelCache
from src.query_router import normalize_query_route
from src.text_parser import parser as text_parser
from src.utils import safe_float


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
            cls._model = LTRModelCache.get_model(model_path)
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
        if stage == "manual":
            ranked = sort_candidates_with_stage_priority(
                candidates,
                primary_score_field=primary_score_field,
            )
        else:
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
    def _item_query_text(item: dict, context: dict | None = None) -> str:
        context = context or {}
        canonical_query = dict(context.get("canonical_query") or item.get("canonical_query") or {})
        return " ".join(
            part
            for part in (
                item.get("name", ""),
                item.get("description", ""),
                canonical_query.get("validation_query", ""),
            )
            if str(part or "").strip()
        ).strip()

    @classmethod
    def _extract_item_params(cls, item: dict, context: dict | None = None) -> dict:
        params = dict(item.get("params") or {})
        if not params:
            params = text_parser.parse(cls._item_query_text(item, context))
        if "conduit_dn" in params and "dn" not in params:
            params["dn"] = params.get("conduit_dn")
        return params

    @classmethod
    def _extract_item_features(cls, item: dict, context: dict | None = None, params: dict | None = None) -> dict:
        existing = item.get("canonical_features")
        if isinstance(existing, dict) and existing:
            return dict(existing)
        params = dict(params or cls._extract_item_params(item, context))
        specialty = str(
            item.get("specialty")
            or item.get("_resolved_specialty")
            or (context or {}).get("specialty")
            or ""
        ).strip()
        return text_parser.parse_canonical(
            cls._item_query_text(item, context) or str(item.get("name", "") or ""),
            specialty=specialty,
            params=params,
        )

    @staticmethod
    def _candidate_query_text(candidate: dict) -> str:
        return " ".join(
            part
            for part in (candidate.get("name", ""), candidate.get("description", ""))
            if str(part or "").strip()
        ).strip()

    @classmethod
    def _extract_candidate_params(cls, candidate: dict) -> dict:
        cached = candidate.get("_ltr_guard_params")
        if isinstance(cached, dict):
            return cached
        params = text_parser.parse(cls._candidate_query_text(candidate))
        for key in (
            "material",
            "connection",
            "install_method",
            "dn",
            "conduit_dn",
            "cable_section",
            "kva",
            "kw",
            "ampere",
            "circuits",
            "port_count",
            "perimeter",
            "half_perimeter",
            "large_side",
        ):
            value = candidate.get(key)
            if value not in (None, "", []):
                params[key] = value
        if "conduit_dn" in params and "dn" not in params:
            params["dn"] = params.get("conduit_dn")
        candidate["_ltr_guard_params"] = params
        return params

    @classmethod
    def _extract_candidate_features(cls, item: dict, candidate: dict, context: dict | None = None) -> dict:
        existing = candidate.get("candidate_canonical_features") or candidate.get("canonical_features")
        if isinstance(existing, dict) and existing:
            return dict(existing)
        context = context or {}
        specialty = str(
            candidate.get("specialty")
            or item.get("specialty")
            or item.get("_resolved_specialty")
            or context.get("specialty")
            or ""
        ).strip()
        province = str(
            item.get("_resolved_province")
            or item.get("province")
            or context.get("province")
            or ""
        ).strip()
        features = build_candidate_canonical_features(
            candidate,
            specialty=specialty,
            province=province,
        )
        candidate.setdefault("candidate_canonical_features", dict(features))
        return dict(features)

    @staticmethod
    def _exact_text_match(left: object, right: object) -> bool:
        left_text = str(left or "").strip()
        right_text = str(right or "").strip()
        return bool(left_text and right_text and left_text == right_text)

    @staticmethod
    def _snapshot_match_flag(row: dict, key: str) -> bool:
        try:
            return int(row.get(key, 0) or 0) > 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _numeric_exact_match(left: object, right: object) -> bool:
        try:
            return abs(float(left) - float(right)) <= 1e-9
        except (TypeError, ValueError):
            return False

    @classmethod
    def _extract_exact_spec_detail(cls, item_params: dict, candidate_params: dict) -> tuple[bool, str, object]:
        for key in (
            "dn",
            "cable_section",
            "kva",
            "kw",
            "ampere",
            "circuits",
            "port_count",
            "perimeter",
            "half_perimeter",
            "large_side",
        ):
            item_value = item_params.get(key)
            candidate_value = candidate_params.get(key)
            if cls._numeric_exact_match(item_value, candidate_value):
                return True, key, item_value
        return False, "", None

    @staticmethod
    def _is_authority_candidate(candidate: dict) -> bool:
        layer = str(candidate.get("experience_layer") or candidate.get("layer") or "").strip().lower()
        if layer == "authority":
            return True
        knowledge_sources = {
            str(value).strip().lower()
            for value in list(candidate.get("knowledge_prior_sources") or [])
            if str(value).strip()
        }
        return bool(
            candidate.get("match_source") == "experience_exact"
            or (candidate.get("match_source") == "experience_injected" and layer == "authority")
            or ("experience" in knowledge_sources and layer == "authority")
        )

    @classmethod
    def _compute_ltr_anchor(cls, item: dict, candidate: dict, context: dict | None = None) -> tuple[float, dict]:
        item_params = cls._extract_item_params(item, context)
        item_features = cls._extract_item_features(item, context, item_params)
        candidate_params = cls._extract_candidate_params(candidate)
        candidate_features = cls._extract_candidate_features(item, candidate, context)

        item_entity = item_features.get("entity") or item_features.get("canonical_name") or ""
        candidate_entity = candidate_features.get("entity") or candidate_features.get("canonical_name") or ""
        entity_match = cls._exact_text_match(item_entity, candidate_entity)

        item_material = item_params.get("material") or item_features.get("material") or ""
        candidate_material = candidate_params.get("material") or candidate_features.get("material") or ""
        material_match = cls._exact_text_match(item_material, candidate_material)

        item_connection = item_params.get("connection") or item_features.get("connection") or ""
        candidate_connection = candidate_params.get("connection") or candidate_features.get("connection") or ""
        connection_match = cls._exact_text_match(item_connection, candidate_connection)

        spec_exact, spec_field, spec_value = cls._extract_exact_spec_detail(item_params, candidate_params)
        authority_match = cls._is_authority_candidate(candidate)

        score = 0.0
        if entity_match:
            score += 4.0
        if material_match:
            score += 2.0
        if connection_match:
            score += 2.0
        if authority_match:
            score += 3.0
        if spec_exact:
            score += 1.0

        details = {
            "entity_exact_match": entity_match,
            "entity_query": str(item_entity or ""),
            "entity_candidate": str(candidate_entity or ""),
            "material_match": material_match,
            "material_query": str(item_material or ""),
            "material_candidate": str(candidate_material or ""),
            "connection_match": connection_match,
            "connection_query": str(item_connection or ""),
            "connection_candidate": str(candidate_connection or ""),
            "authority_experience": authority_match,
            "experience_layer": str(candidate.get("experience_layer") or candidate.get("layer") or ""),
            "spec_exact_match": spec_exact,
            "spec_field": spec_field,
            "spec_value": spec_value,
        }
        return score, details

    @classmethod
    def _apply_snapshot_struct_guard(
        cls,
        incumbent: dict,
        challenger: dict,
    ) -> tuple[bool, str, dict]:
        incumbent_row = incumbent.get("ltr_feature_snapshot") or {}
        challenger_row = challenger.get("ltr_feature_snapshot") or {}

        incumbent_entity = cls._snapshot_match_flag(incumbent_row, "entity_match")
        incumbent_canonical = cls._snapshot_match_flag(incumbent_row, "canonical_name_match")
        incumbent_system = cls._snapshot_match_flag(incumbent_row, "system_match")
        incumbent_family = cls._snapshot_match_flag(incumbent_row, "family_match")
        challenger_entity = cls._snapshot_match_flag(challenger_row, "entity_match")
        challenger_canonical = cls._snapshot_match_flag(challenger_row, "canonical_name_match")
        challenger_system = cls._snapshot_match_flag(challenger_row, "system_match")
        challenger_family = cls._snapshot_match_flag(challenger_row, "family_match")
        challenger_conflict = (
            cls._snapshot_match_flag(challenger_row, "entity_conflict")
            or cls._snapshot_match_flag(challenger_row, "canonical_name_conflict")
        )

        incumbent_feature = safe_float(incumbent.get("feature_alignment_score"), 0.0)
        challenger_feature = safe_float(challenger.get("feature_alignment_score"), 0.0)

        details = {
            "incumbent_entity_match": incumbent_entity,
            "incumbent_canonical_name_match": incumbent_canonical,
            "incumbent_system_match": incumbent_system,
            "incumbent_family_match": incumbent_family,
            "incumbent_feature_alignment_score": incumbent_feature,
            "challenger_entity_match": challenger_entity,
            "challenger_canonical_name_match": challenger_canonical,
            "challenger_system_match": challenger_system,
            "challenger_family_match": challenger_family,
            "challenger_feature_alignment_score": challenger_feature,
            "challenger_struct_conflict": challenger_conflict,
        }

        if incumbent_entity and challenger_conflict:
            return True, "challenger_struct_conflict", details

        if (
            incumbent_entity
            and incumbent_canonical
            and incumbent_system
            and incumbent_feature >= 0.95
            and not (challenger_entity or challenger_canonical)
        ):
            return True, "snapshot_exact_anchor_dominates", details

        if (
            incumbent_entity
            and incumbent_family
            and incumbent_system
            and not challenger_family
            and not challenger_system
            and incumbent_feature >= challenger_feature + 0.10
        ):
            return True, "family_system_anchor_dominates", details

        return False, "", details

    @classmethod
    def _apply_ltr_guard(
        cls,
        item: dict,
        manual_ranked: list[dict],
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[list[dict], dict]:
        threshold = float(getattr(config, "LTR_GUARD_THRESHOLD", 6.0) or 0.0)
        meta = {
            "enabled": bool(getattr(config, "LTR_GUARD_ENABLED", True)),
            "threshold": threshold,
            "action": "not_needed",
            "reason": "",
            "anchor_score": 0.0,
            "anchor_details": {},
            "pre_ltr_top1_id": str((manual_ranked[0].get("quota_id", "") if manual_ranked else "") or ""),
            "ltr_wanted_top1_id": str((ltr_ranked[0].get("quota_id", "") if ltr_ranked else "") or ""),
            "final_top1_id": str((ltr_ranked[0].get("quota_id", "") if ltr_ranked else "") or ""),
        }
        if not meta["enabled"]:
            meta["action"] = "disabled"
            return ltr_ranked, meta
        if not manual_ranked or not ltr_ranked:
            meta["action"] = "missing_candidates"
            return ltr_ranked, meta

        incumbent = manual_ranked[0]
        challenger = ltr_ranked[0]
        incumbent_id = str(incumbent.get("quota_id", "") or "").strip()
        challenger_id = str(challenger.get("quota_id", "") or "").strip()
        if not incumbent_id or not challenger_id or incumbent_id == challenger_id:
            meta["action"] = "no_change"
            meta["reason"] = "same_top1"
            return ltr_ranked, meta

        anchor_score, anchor_details = cls._compute_ltr_anchor(item, incumbent, context)
        meta["anchor_score"] = anchor_score
        meta["anchor_details"] = anchor_details

        snapshot_guard_blocked, snapshot_reason, snapshot_details = cls._apply_snapshot_struct_guard(
            incumbent,
            challenger,
        )
        route_profile = (
            (context or {}).get("route_profile")
            or (context or {}).get("query_route")
            or item.get("query_route")
            or {}
        )
        route = normalize_query_route(route_profile)
        incumbent_manual_score = safe_float(incumbent.get("manual_structured_score"), 0.0)
        challenger_manual_score = safe_float(challenger.get("manual_structured_score"), 0.0)
        manual_margin = incumbent_manual_score - challenger_manual_score
        incumbent_scope_match = safe_float(incumbent.get("candidate_scope_match"), 0.0)
        challenger_scope_match = safe_float(challenger.get("candidate_scope_match"), 0.0)
        incumbent_scope_conflict = bool(incumbent.get("candidate_scope_conflict"))
        challenger_scope_conflict = bool(challenger.get("candidate_scope_conflict"))
        meta["snapshot_guard"] = {
            "blocked": snapshot_guard_blocked,
            "reason": snapshot_reason,
            "details": snapshot_details,
        }
        meta["route"] = route
        meta["manual_margin"] = manual_margin
        meta["scope_guard"] = {
            "incumbent_scope_match": incumbent_scope_match,
            "incumbent_scope_conflict": incumbent_scope_conflict,
            "challenger_scope_match": challenger_scope_match,
            "challenger_scope_conflict": challenger_scope_conflict,
        }
        if snapshot_guard_blocked:
            guarded_incumbent = cls._find_candidate_by_quota_id(ltr_ranked, incumbent_id) or incumbent
            guarded_incumbent["_rank_score_source"] = "manual"
            guarded_incumbent["ltr_guard_blocked"] = True
            guarded_incumbent["ltr_guard_anchor_score"] = anchor_score
            guarded = [guarded_incumbent] + [
                candidate
                for candidate in ltr_ranked
                if str(candidate.get("quota_id", "") or "").strip() != incumbent_id
            ]
            meta["action"] = "blocked"
            meta["reason"] = snapshot_reason
            meta["final_top1_id"] = incumbent_id
            return guarded, meta

        if (
            incumbent_scope_match > challenger_scope_match
            and incumbent_scope_match >= 1.0
            and challenger_scope_conflict
        ):
            guarded_incumbent = cls._find_candidate_by_quota_id(ltr_ranked, incumbent_id) or incumbent
            guarded_incumbent["_rank_score_source"] = "manual"
            guarded_incumbent["ltr_guard_blocked"] = True
            guarded_incumbent["ltr_guard_anchor_score"] = anchor_score
            guarded = [guarded_incumbent] + [
                candidate
                for candidate in ltr_ranked
                if str(candidate.get("quota_id", "") or "").strip() != incumbent_id
            ]
            meta["action"] = "blocked"
            meta["reason"] = "scope_match_protected"
            meta["final_top1_id"] = incumbent_id
            return guarded, meta

        if route in {"material", "semantic_description", "ambiguous_short"} and manual_margin >= 0.06:
            guarded_incumbent = cls._find_candidate_by_quota_id(ltr_ranked, incumbent_id) or incumbent
            guarded_incumbent["_rank_score_source"] = "manual"
            guarded_incumbent["ltr_guard_blocked"] = True
            guarded_incumbent["ltr_guard_anchor_score"] = anchor_score
            guarded = [guarded_incumbent] + [
                candidate
                for candidate in ltr_ranked
                if str(candidate.get("quota_id", "") or "").strip() != incumbent_id
            ]
            meta["action"] = "blocked"
            meta["reason"] = "weak_route_manual_margin"
            meta["final_top1_id"] = incumbent_id
            return guarded, meta

        if anchor_score < threshold:
            meta["action"] = "allowed"
            meta["reason"] = "anchor_below_threshold"
            return ltr_ranked, meta

        guarded_incumbent = cls._find_candidate_by_quota_id(ltr_ranked, incumbent_id) or incumbent
        guarded_incumbent["_rank_score_source"] = "manual"
        guarded_incumbent["ltr_guard_blocked"] = True
        guarded_incumbent["ltr_guard_anchor_score"] = anchor_score
        guarded = [guarded_incumbent] + [
            candidate
            for candidate in ltr_ranked
            if str(candidate.get("quota_id", "") or "").strip() != incumbent_id
        ]
        meta["action"] = "blocked"
        meta["reason"] = "strong_anchor_protected"
        meta["final_top1_id"] = incumbent_id
        return guarded, meta

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
            "raw_ltr_top1_id": str((candidates or [{}])[0].get("quota_id", "") or "") if candidates else "",
            "post_ltr_top1_id": str((candidates or [{}])[0].get("quota_id", "") or "") if candidates else "",
            "post_cgr_top1_id": str((candidates or [{}])[0].get("quota_id", "") or "") if candidates else "",
            "feature_count": 0,
            "cgr": {},
            "ltr_guard": {
                "enabled": bool(getattr(config, "LTR_GUARD_ENABLED", True)),
                "threshold": float(getattr(config, "LTR_GUARD_THRESHOLD", 6.0) or 0.0),
                "action": "not_run",
                "snapshot_guard": {
                    "blocked": False,
                    "reason": "",
                    "details": {},
                },
            },
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
        manual_ranked = list(ranked)
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
            ltr_ranked = cls._sort_with_stage_priority(
                ranked,
                stage="ltr",
                primary_score_field="ltr_score",
            )
            meta["raw_ltr_top1_id"] = str(ltr_ranked[0].get("quota_id", "") or "") if ltr_ranked else ""
            ranked, ltr_guard_meta = cls._apply_ltr_guard(item, manual_ranked, ltr_ranked, context)
            meta["applied"] = True
            meta["ltr_guard"] = ltr_guard_meta
            meta["post_ltr_top1_id"] = str(ranked[0].get("quota_id", "") or "") if ranked else ""
            if ltr_guard_meta.get("action") == "blocked":
                meta["primary_stage"] = "ltr_guard"
            elif meta["post_ltr_top1_id"] and meta["post_ltr_top1_id"] != meta["post_manual_top1_id"]:
                meta["primary_stage"] = "ltr"
            else:
                meta["primary_stage"] = "manual"
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
