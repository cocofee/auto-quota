from __future__ import annotations

import json
import re
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
    def _quota_major_prefix(quota_id: object) -> str:
        text = str(quota_id or "").strip().upper()
        if not text:
            return ""
        if "-" in text:
            prefix = text.split("-", 1)[0]
        else:
            prefix = "".join(ch for ch in text if ch.isdigit()) or text
        if prefix.startswith("C") and prefix[1:].isdigit():
            return prefix[1:]
        return prefix

    @staticmethod
    def _surface_pair_base(text: str) -> str:
        base = str(text or "")
        for term in ("\u5e73\u9762", "\u7acb\u9762"):
            base = base.replace(term, "")
        return "".join(base.split())

    @staticmethod
    def _traffic_arrow_spec(text: str) -> tuple[str, str]:
        normalized = str(text or "").replace(" ", "").lower()
        direction = ""
        for term in ("\u76f4\u884c\u8f6c\u5f2f", "\u76f4\u884c\u6389\u5934", "\u8f6c\u5f2f", "\u76f4\u884c", "\u6389\u5934"):
            if term in normalized:
                direction = term
                break
        length = ""
        match = re.search(r"(\d+(?:\.\d+)?)m", normalized)
        if match:
            length = match.group(1)
        return direction, length

    @staticmethod
    def _bitumen_layer_intent(text: str) -> dict:
        normalized = str(text or "").replace(" ", "")
        lower = normalized.lower()
        explicit_tack = any(
            term in lower
            for term in (
                "pc-3",
                "\u4e73\u5316\u6ca5\u9752\u7c98\u5c42",
                "\u4e73\u5316\u6ca5\u9752\u9ecf\u5c42",
                "\u7c98\u5c42\u7528\u91cf",
                "\u9ecf\u5c42\u7528\u91cf",
            )
        )
        explicit_prime = any(
            term in normalized
            for term in (
                "\u900f\u6cb9\u5c42",
                "\u4e73\u5316\u6ca5\u9752\u900f\u5c42",
                "\u900f\u5c42\uff1a",
                "\u900f\u5c42:",
                "\u8bbe\u7f6e\u4e73\u5316\u6ca5\u9752\u900f\u5c42",
            )
        )
        wants_tack = explicit_tack
        wants_prime = not wants_tack and (explicit_prime or "\u900f\u5c42" in normalized)
        return {
            "wants_prime": wants_prime,
            "wants_tack": wants_tack,
            "emulsified": "\u4e73\u5316\u6ca5\u9752" in normalized,
            "semi_rigid": any(term in normalized for term in ("\u534a\u521a\u6027", "\u6c34\u6ce5\u7a33\u5b9a")),
        }

    @staticmethod
    def _water_stabilized_paver_intent(text: str) -> dict:
        normalized = str(text or "").replace(" ", "")
        return {
            "water_stabilized": "\u6c34\u6ce5\u7a33\u5b9a" in normalized,
            "paver": "\u644a\u94fa\u673a" in normalized,
            "thick_layer": "\u539a" in normalized and "\u6bcf\u51cf" not in normalized and "\u6bcf\u589e\u51cf" not in normalized,
        }

    @staticmethod
    def _curb_stone_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "")
        specialty_text = str(specialty or "").strip().upper()
        wants_side = "\u4fa7\u77f3" in normalized
        wants_flat = "\u5e73\u77f3" in normalized
        wants_curb = wants_side or wants_flat or "\u7f18\u77f3" in normalized
        return {
            "municipal": specialty_text in {"C2", "2"},
            "install": "\u5b89\u780c" in normalized,
            "wants_side": wants_side,
            "wants_flat": wants_flat,
            "wants_curb": wants_curb,
            "demolition": "\u62c6\u9664" in normalized or "\u7ffb\u6316" in normalized,
        }

    @staticmethod
    def _portal_frame_sign_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "")
        specialty_text = str(specialty or "").strip().upper()
        wants_portal_frame = "\u95e8\u5f0f\u67b6" in normalized
        cantilever_only = "\u60ac\u81c2" in normalized and not wants_portal_frame
        support_only = any(term in normalized for term in ("\u94a2\u652f\u6491", "\u652f\u6491\u67b6")) and not wants_portal_frame
        return {
            "municipal": specialty_text in {"C2", "2"},
            "wants_portal_frame": wants_portal_frame,
            "cantilever_only": cantilever_only,
            "support_only": support_only,
        }

    @staticmethod
    def _precast_laminated_slab_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_laminated = "\u53e0\u5408" in normalized and ("\u697c\u677f" in normalized or "\u677f" in normalized)
        wants_precast = "PC" in normalized or "\u88c5\u914d\u5f0f" in normalized or "\u9884\u5236" in normalized
        wall_panel = any(term in normalized for term in ("\u5916\u5899", "\u5899\u677f", "PCF"))
        return {
            "building": specialty_text in {"C5", "5"},
            "wants_laminated_slab": wants_laminated and wants_precast and not wall_panel,
            "wall_panel": wall_panel,
        }

    @staticmethod
    def _concrete_foundation_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "")
        specialty_text = str(specialty or "").strip().upper()
        wants_concrete_foundation = "\u6df7\u51dd\u571f\u57fa\u7840" in normalized
        explicit_concrete = bool(re.search(r"C\d{2}", normalized, flags=re.IGNORECASE)) or "\u5546\u54c1\u6df7\u51dd\u571f" in normalized
        stone_foundation = any(term in normalized for term in ("\u5757\u77f3", "\u7247\u77f3", "\u6bdb\u77f3"))
        return {
            "bridge": specialty_text in {"C3", "3"},
            "wants_concrete_foundation": wants_concrete_foundation,
            "explicit_concrete": explicit_concrete,
            "stone_foundation": stone_foundation,
        }

    @staticmethod
    def _foam_expansion_joint_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "")
        specialty_text = str(specialty or "").strip().upper()
        wants_joint = "\u53d8\u5f62\u7f1d" in normalized or "\u540e\u6d47\u5e26" in normalized or "\u5d4c\u586b\u7f1d" in normalized
        wants_foam = any(term in normalized for term in ("\u6ce1\u6cab\u5851\u6599", "\u805a\u82ef\u4e59\u70ef", "\u6ce1\u6cab\u677f"))
        oil_hemp = "\u6cb9\u6d78\u9ebb\u4e1d" in normalized
        return {
            "waterproof": specialty_text in {"C9", "9"},
            "wants_joint": wants_joint,
            "wants_foam": wants_foam,
            "oil_hemp": oil_hemp,
        }

    @staticmethod
    def _self_adhesive_polymer_membrane_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_membrane = "\u5377\u6750" in normalized and "\u9632\u6c34" in normalized
        wants_self_adhesive = "\u81ea\u7c98" in normalized
        wants_polymer = "\u9ad8\u5206\u5b50" in normalized
        modified_bitumen = "\u6539\u6027\u6ca5\u9752" in normalized or "\u6ca5\u9752" in normalized
        wants_horizontal = any(
            term in normalized
            for term in ("\u5c4b\u9762", "\u697c\uff08\u5730\uff09\u9762", "\u697c\u5730\u9762", "\u5730\u9762")
        )
        wants_vertical = "\u5899\u9762" in normalized or "\u7acb\u9762" in normalized
        return {
            "waterproof": specialty_text in {"C9", "9"},
            "wants_membrane": wants_membrane,
            "wants_self_adhesive": wants_self_adhesive,
            "wants_polymer": wants_polymer,
            "modified_bitumen": modified_bitumen,
            "wants_horizontal": wants_horizontal and not wants_vertical,
            "wants_vertical": wants_vertical,
        }

    @staticmethod
    def _cementitious_crystalline_waterproof_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_crystalline = "\u6c34\u6ce5\u57fa" in normalized and "\u6e17\u900f\u7ed3\u6676" in normalized and "\u9632\u6c34" in normalized
        wants_coating = "\u6d82\u6599" in normalized or "\u6d82\u819c" in normalized
        polymer_cement = "\u805a\u5408\u7269\u6c34\u6ce5" in normalized or "JS" in normalized
        other_material = any(term in normalized for term in ("\u6539\u6027\u6ca5\u9752", "\u6ca5\u9752", "\u805a\u6c28\u916f"))
        wants_horizontal = any(
            term in normalized
            for term in ("\u5c4b\u9762", "\u697c\uff08\u5730\uff09\u9762", "\u697c\u5730\u9762", "\u5730\u9762", "\u6869\u5934")
        )
        wants_vertical = "\u5899\u9762" in normalized or "\u7acb\u9762" in normalized
        one_mm = bool(re.search(r"1(?:\.0)?\s*MM", normalized, flags=re.IGNORECASE)) or "\u539a\u5ea61.0MM" in normalized
        return {
            "waterproof": specialty_text in {"C9", "9"},
            "wants_crystalline": wants_crystalline,
            "wants_coating": wants_coating,
            "polymer_cement": polymer_cement,
            "other_material": other_material,
            "wants_horizontal": wants_horizontal and not wants_vertical,
            "wants_vertical": wants_vertical,
            "one_mm": one_mm,
        }

    @staticmethod
    def _polymer_cement_waterproof_coating_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_polymer_cement = "\u805a\u5408\u7269\u6c34\u6ce5" in normalized or "JS" in normalized
        wants_coating = "\u6d82\u6599" in normalized or "\u6d82\u819c" in normalized
        horizontal_anchor = any(
            term in normalized
            for term in ("\u5c4b\u9762", "\u697c\uff08\u5730\uff09\u9762", "\u697c\u5730\u9762", "\u697c\u9762", "\u5730\u9762")
        )
        local_upturn = any(term in normalized for term in ("\u9047\u4fa7\u5899", "\u4e0a\u7ffb", "\u7ffb\u81f3\u7acb\u9762", "\u53cd\u81f3\u7acb\u9762"))
        vertical_only = any(term in normalized for term in ("\u5899\u9762", "\u7acb\u9762")) and not local_upturn
        increment_only = any(term in normalized for term in ("\u6bcf\u589e", "\u6bcf\u51cf", "\u589e\u52a0\u539a\u5ea6", "\u589e\u539a"))
        other_material = any(term in normalized for term in ("\u6539\u6027\u6ca5\u9752", "\u6ca5\u9752", "\u805a\u6c28\u916f", "\u6e17\u900f\u7ed3\u6676"))
        return {
            "waterproof": specialty_text in {"C9", "9"},
            "wants_polymer_cement": wants_polymer_cement,
            "wants_coating": wants_coating,
            "horizontal_anchor": horizontal_anchor,
            "local_upturn": local_upturn,
            "vertical_only": vertical_only,
            "increment_only": increment_only,
            "other_material": other_material,
        }

    @staticmethod
    def _embedded_iron_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_embedded_iron = any(term in normalized for term in ("\u9884\u57cb\u94c1\u4ef6", "\u9884\u57cb\u4ef6", "\u57cb\u4ef6"))
        has_plate_anchor = any(term in normalized for term in ("\u951a\u677f", "\u951a\u7b4b", "\u94a2\u677f", "\u7aef\u677f", "\u5de5\u5b57\u94a2"))
        bolt_only = "\u9884\u57cb\u87ba\u6813" in normalized or (
            "\u5730\u811a\u87ba\u6813" in normalized and not wants_embedded_iron
        )
        above_25kg = bool(re.search(r"25\s*KG[\/\uff0f]?\u5757?\u4ee5\u4e0a", normalized, flags=re.IGNORECASE)) or "\u91cd\u91cf25KG\u4ee5\u4e0a" in normalized
        within_25kg = bool(re.search(r"25\s*KG[\/\uff0f]?\u5757?\u4ee5\u5185", normalized, flags=re.IGNORECASE))
        return {
            "building": specialty_text in {"C5", "5"},
            "road": specialty_text in {"C1", "1"},
            "wants_embedded_iron": wants_embedded_iron,
            "has_plate_anchor": has_plate_anchor,
            "bolt_only": bolt_only,
            "above_25kg": above_25kg,
            "within_25kg": within_25kg,
        }

    @staticmethod
    def _postcast_rebar_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_postcast = "\u540e\u6d47" in normalized and "\u94a2\u7b4b" in normalized
        wants_deformed = "HRB400" in normalized or "\u5e26\u808b\u94a2\u7b4b" in normalized or "\u70ed\u8f67\u5e26\u808b\u94a2\u7b4b" in normalized
        hoop = "\u7b8d\u7b4b" in normalized
        round_steel = "HPB300" in normalized or "\u5706\u94a2\u7b4b" in normalized
        diameter_values = [
            float(match.group(1))
            for match in re.finditer(
                r"(?:[\u03a6\u0424]\s*=?\s*|\u76f4\u5f84(?:\u4e3a)?\s*=?\s*)(\d+(?:\.\d+)?)",
                normalized,
            )
        ]
        diameter_le_10 = bool(diameter_values) and min(diameter_values) <= 10.0
        return {
            "building": specialty_text in {"C5", "5"},
            "wants_postcast": wants_postcast,
            "wants_deformed": wants_deformed,
            "hoop": hoop,
            "round_steel": round_steel,
            "diameter_values": diameter_values,
            "diameter_le_10": diameter_le_10,
        }

    @staticmethod
    def _manhole_surround_backfill_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "")
        specialty_text = str(specialty or "").strip().upper()
        wants_manhole_surround = "\u68c0\u67e5\u4e95" in normalized and "\u56db\u5468" in normalized and "\u56de\u586b" in normalized
        wants_gravel = "\u7ea7\u914d\u7802\u783e\u77f3" in normalized or ("\u7802\u783e\u77f3" in normalized and "\u56de\u586b" in normalized)
        artificial_grade = "\u4eba\u5de5\u7ea7\u914d" in normalized
        bedding = "\u57ab\u5c42" in normalized
        return {
            "drainage": specialty_text in {"C6", "6"},
            "wants_manhole_surround": wants_manhole_surround,
            "wants_gravel": wants_gravel,
            "artificial_grade": artificial_grade,
            "bedding": bedding,
        }

    @staticmethod
    def _sinking_well_bottom_slab_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_sinking_well = "\u6c89\u4e95" in normalized
        wants_bottom_slab = "\u5e95\u677f" in normalized
        concrete = "\u6df7\u51dd\u571f" in normalized or bool(re.search(r"\bC\d{2}\b", normalized))
        bedding = "\u57ab\u5c42" in normalized
        template = "\u6a21\u677f" in normalized
        cover = "\u4e95\u76d6" in normalized
        platform = "\u5e73\u53f0" in normalized and not wants_bottom_slab
        within_50 = "\u539a\u5ea650CM\u4ee5\u5185" in normalized or "\u539a\u5ea6\u8981\u6c42:\u539a\u5ea650CM\u4ee5\u5185" in normalized
        over_50 = "\u539a\u5ea650CM\u5916" in normalized or "\u539a\u5ea650CM\u4ee5\u5916" in normalized
        return {
            "drainage": specialty_text in {"C6", "6"},
            "wants_sinking_well": wants_sinking_well,
            "wants_bottom_slab": wants_bottom_slab,
            "concrete": concrete,
            "bedding": bedding,
            "template": template,
            "cover": cover,
            "platform": platform,
            "within_50": within_50,
            "over_50": over_50,
        }

    @staticmethod
    def _surplus_soil_disposal_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "")
        specialty_text = str(specialty or "").strip().upper()
        wants_surplus = "\u4f59\u65b9\u5f03\u7f6e" in normalized or "\u591a\u4f59\u571f\u65b9" in normalized
        wants_haul = "\u5916\u8fd0" in normalized or "\u8fd0\u8ddd" in normalized or "\u88c5\u8f66" in normalized
        soil_context = "\u571f\u65b9" in normalized or "\u571f\u77f3\u65b9" in normalized
        stone_only = "\u77f3\u65b9\u5916\u8fd0" in normalized and "\u571f\u77f3\u65b9" not in normalized
        backfill = "\u56de\u586b" in normalized and not wants_surplus
        return {
            "road": specialty_text in {"C1", "1"},
            "wants_surplus": wants_surplus,
            "wants_haul": wants_haul,
            "soil_context": soil_context,
            "stone_only": stone_only,
            "backfill": backfill,
        }

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
    def _text_has_any(text: str, keywords: tuple[str, ...]) -> bool:
        normalized = str(text or "").strip().lower()
        return bool(normalized and any(keyword in normalized for keyword in keywords))

    @classmethod
    def _detect_explicit_semantic_advantage(
        cls,
        item: dict,
        incumbent: dict,
        challenger: dict,
        context: dict | None = None,
    ) -> tuple[bool, str, dict]:
        item_text = cls._item_query_text(item, context)
        incumbent_text = cls._candidate_query_text(incumbent)
        challenger_text = cls._candidate_query_text(challenger)

        details = {
            "item_text": item_text,
            "incumbent_text": incumbent_text,
            "challenger_text": challenger_text,
            "signals": [],
        }

        def _record(signal: str) -> None:
            details["signals"].append(signal)

        if (
            "室外" not in item_text
            and cls._text_has_any(incumbent_text, ("室外",))
            and not cls._text_has_any(challenger_text, ("室外",))
        ):
            _record("indoor_default_vs_outdoor_incumbent")

        if (
            cls._text_has_any(item_text, ("雨水",))
            and cls._text_has_any(challenger_text, ("雨水",))
            and not cls._text_has_any(incumbent_text, ("雨水",))
        ):
            _record("rainwater_keyword_alignment")

        if (
            cls._text_has_any(item_text, ("排水",))
            and cls._text_has_any(challenger_text, ("排水",))
            and not cls._text_has_any(incumbent_text, ("排水",))
        ):
            _record("drainage_keyword_alignment")

        if (
            cls._text_has_any(item_text, ("阻火圈",))
            and cls._text_has_any(challenger_text, ("阻火圈",))
            and not cls._text_has_any(incumbent_text, ("阻火圈",))
        ):
            _record("firestop_ring_keyword_alignment")

        if (
            cls._text_has_any(item_text, ("\u690d\u7b4b",))
            and cls._text_has_any(challenger_text, ("\u690d\u7b4b",))
            and not cls._text_has_any(incumbent_text, ("\u690d\u7b4b",))
        ):
            _record("rebar_planting_keyword_alignment")

        if (
            cls._text_has_any(item_text, ("刚性防水套管", "柔性防水套管", "填料套管", "套管"))
            and cls._text_has_any(
                challenger_text,
                ("刚性防水套管", "柔性防水套管", "填料套管", "套管"),
            )
            and not cls._text_has_any(
                incumbent_text,
                ("刚性防水套管", "柔性防水套管", "填料套管", "套管"),
            )
        ):
            _record("sleeve_keyword_alignment")

        plastic_signal = cls._text_has_any(item_text, ("upvc", "pvc", "ppr", "pe", "hdpe", "塑料"))
        challenger_plastic = cls._text_has_any(challenger_text, ("upvc", "pvc", "ppr", "pe", "hdpe", "塑料"))
        incumbent_metal = cls._text_has_any(incumbent_text, ("铸铁", "镀锌", "钢", "铜", "不锈钢"))
        if plastic_signal and challenger_plastic and incumbent_metal:
            _record("plastic_query_vs_metal_incumbent")

        cast_iron_signal = cls._text_has_any(item_text, ("铸铁",))
        challenger_cast_iron = cls._text_has_any(challenger_text, ("铸铁",))
        incumbent_plastic = cls._text_has_any(incumbent_text, ("upvc", "pvc", "ppr", "pe", "hdpe", "塑料"))
        if cast_iron_signal and challenger_cast_iron and incumbent_plastic:
            _record("cast_iron_query_vs_plastic_incumbent")

        item_arrow_direction, item_arrow_length = cls._traffic_arrow_spec(item_text)
        challenger_arrow_direction, challenger_arrow_length = cls._traffic_arrow_spec(challenger_text)
        incumbent_arrow_direction, incumbent_arrow_length = cls._traffic_arrow_spec(incumbent_text)
        arrow_direction_match = bool(
            item_arrow_direction
            and item_arrow_direction == challenger_arrow_direction
            and item_arrow_direction != incumbent_arrow_direction
        )
        arrow_length_match = bool(
            item_arrow_length
            and item_arrow_length == challenger_arrow_length
            and item_arrow_length != incumbent_arrow_length
        )
        if "\u7bad\u5934" in item_text and "\u7bad\u5934" in challenger_text and arrow_direction_match and arrow_length_match:
            details["traffic_arrow_spec"] = {
                "item": [item_arrow_direction, item_arrow_length],
                "incumbent": [incumbent_arrow_direction, incumbent_arrow_length],
                "challenger": [challenger_arrow_direction, challenger_arrow_length],
            }
            _record("traffic_arrow_spec_alignment")

        if details["signals"]:
            return True, "challenger_explicit_semantic_advantage", details
        return False, "", details

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
    def _apply_pre_ltr_stability_guard(
        cls,
        incumbent: dict,
        challenger: dict,
    ) -> tuple[bool, str, dict]:
        incumbent_row = incumbent.get("ltr_feature_snapshot") or {}
        challenger_row = challenger.get("ltr_feature_snapshot") or {}

        incumbent_struct_matches = sum(
            1
            for key in ("entity_match", "canonical_name_match", "system_match", "family_match")
            if cls._snapshot_match_flag(incumbent_row, key)
        )
        challenger_struct_matches = sum(
            1
            for key in ("entity_match", "canonical_name_match", "system_match", "family_match")
            if cls._snapshot_match_flag(challenger_row, key)
        )
        incumbent_feature = safe_float(incumbent.get("feature_alignment_score"), 0.0)
        challenger_feature = safe_float(challenger.get("feature_alignment_score"), 0.0)
        incumbent_rerank = safe_float(incumbent.get("rerank_score"), 0.0)
        challenger_rerank = safe_float(challenger.get("rerank_score"), 0.0)
        incumbent_semantic_z = safe_float(incumbent_row.get("semantic_rerank_zscore"), 0.0)
        challenger_semantic_z = safe_float(challenger_row.get("semantic_rerank_zscore"), 0.0)

        details = {
            "incumbent_struct_matches": incumbent_struct_matches,
            "challenger_struct_matches": challenger_struct_matches,
            "incumbent_feature_alignment_score": incumbent_feature,
            "challenger_feature_alignment_score": challenger_feature,
            "incumbent_rerank_score": incumbent_rerank,
            "challenger_rerank_score": challenger_rerank,
            "incumbent_semantic_zscore": incumbent_semantic_z,
            "challenger_semantic_zscore": challenger_semantic_z,
        }

        if (
            incumbent_struct_matches >= 2
            and challenger_struct_matches == 0
            and incumbent_feature >= challenger_feature + 0.25
            and incumbent_rerank >= challenger_rerank + 0.05
            and incumbent_semantic_z >= challenger_semantic_z + 0.50
        ):
            return True, "pre_ltr_structural_stability", details

        return False, "", details

    @classmethod
    def _apply_surface_orientation_guard(
        cls,
        item: dict,
        incumbent: dict,
        challenger: dict,
        context: dict | None = None,
    ) -> tuple[bool, str, dict]:
        item_text = cls._item_query_text(item, context)
        incumbent_text = cls._candidate_query_text(incumbent)
        challenger_text = cls._candidate_query_text(challenger)

        vertical_terms = ("\u5899\u9762", "\u7acb\u9762")
        horizontal_terms = (
            "\u5c4b\u9762",
            "\u697c\uff08\u5730\uff09\u9762",
            "\u697c\u5730\u9762",
            "\u5730\u9762",
            "\u9876\u68da",
        )
        vertical_surface = "\u7acb\u9762"
        horizontal_surface = "\u5e73\u9762"

        wants_vertical = any(term in item_text for term in vertical_terms)
        wants_horizontal = not wants_vertical and any(term in item_text for term in horizontal_terms)
        incumbent_vertical = vertical_surface in incumbent_text
        incumbent_horizontal = horizontal_surface in incumbent_text
        challenger_vertical = vertical_surface in challenger_text
        challenger_horizontal = horizontal_surface in challenger_text

        details = {
            "item_text": item_text,
            "incumbent_text": incumbent_text,
            "challenger_text": challenger_text,
            "wants_vertical": wants_vertical,
            "wants_horizontal": wants_horizontal,
            "incumbent_vertical": incumbent_vertical,
            "incumbent_horizontal": incumbent_horizontal,
            "challenger_vertical": challenger_vertical,
            "challenger_horizontal": challenger_horizontal,
        }

        if wants_vertical and incumbent_vertical and challenger_horizontal:
            return True, "surface_orientation_protected", details
        if wants_horizontal and incumbent_horizontal and challenger_vertical:
            return True, "surface_orientation_protected", details
        return False, "", details

    @classmethod
    def _apply_surface_orientation_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        vertical_terms = ("\u5899\u9762", "\u7acb\u9762")
        horizontal_terms = (
            "\u5c4b\u9762",
            "\u697c\uff08\u5730\uff09\u9762",
            "\u697c\u5730\u9762",
            "\u5730\u9762",
            "\u9876\u68da",
        )
        vertical_surface = "\u7acb\u9762"
        horizontal_surface = "\u5e73\u9762"

        wants_vertical = any(term in item_text for term in vertical_terms)
        wants_horizontal = not wants_vertical and any(term in item_text for term in horizontal_terms)
        if not wants_vertical and not wants_horizontal:
            return False, "", {"item_text": item_text}, ltr_ranked

        desired_surface = vertical_surface if wants_vertical else horizontal_surface
        rejected_surface = horizontal_surface if wants_vertical else vertical_surface
        top = ltr_ranked[0]
        top_text = cls._candidate_query_text(top)
        if desired_surface in top_text or rejected_surface not in top_text:
            return False, "", {"item_text": item_text, "top_text": top_text}, ltr_ranked

        top_prefix = cls._quota_major_prefix(top.get("quota_id"))
        top_surface_base = cls._surface_pair_base(top_text)
        top_param = safe_float(top.get("param_score"), 0.0)
        top_rerank = safe_float(top.get("rerank_score"), 0.0)
        inspected: list[dict] = []
        for rank, candidate in enumerate(ltr_ranked[1:5], start=2):
            candidate_text = cls._candidate_query_text(candidate)
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            candidate_surface_base = cls._surface_pair_base(candidate_text)
            candidate_param = safe_float(candidate.get("param_score"), 0.0)
            candidate_rerank = safe_float(candidate.get("rerank_score"), 0.0)
            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "surface_pair_base": candidate_surface_base,
                "param_score": candidate_param,
                "rerank_score": candidate_rerank,
            })
            if candidate_prefix != top_prefix:
                continue
            if desired_surface not in candidate_text:
                continue
            if candidate_surface_base != top_surface_base:
                continue
            if candidate_param < top_param - 0.05:
                continue
            if candidate_rerank < top_rerank - 0.20:
                continue

            rescued = [candidate] + [item for item in ltr_ranked if item is not candidate]
            details = {
                "item_text": item_text,
                "desired_surface": desired_surface,
                "rejected_surface": rejected_surface,
                "top_quota_id": str(top.get("quota_id") or ""),
                "top_text": top_text,
                "top_prefix": top_prefix,
                "top_surface_pair_base": top_surface_base,
                "top_param_score": top_param,
                "top_rerank_score": top_rerank,
                "rescued_quota_id": str(candidate.get("quota_id") or ""),
                "rescued_text": candidate_text,
                "rescued_surface_pair_base": candidate_surface_base,
                "rescued_rank": rank,
                "rescued_param_score": candidate_param,
                "rescued_rerank_score": candidate_rerank,
                "inspected": inspected,
            }
            return True, "surface_orientation_rescued", details, rescued

        return False, "", {
            "item_text": item_text,
            "desired_surface": desired_surface,
            "rejected_surface": rejected_surface,
            "top_quota_id": str(top.get("quota_id") or ""),
            "top_text": top_text,
            "top_prefix": top_prefix,
            "top_surface_pair_base": top_surface_base,
            "inspected": inspected,
        }, ltr_ranked

    @classmethod
    def _apply_bitumen_layer_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        intent = cls._bitumen_layer_intent(item_text)
        if not intent["emulsified"] or not (intent["wants_prime"] or intent["wants_tack"]):
            return False, "", {"item_text": item_text, "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "")
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            score = 0
            if candidate_prefix != "2":
                score -= 20
            if "\u4e73\u5316\u6ca5\u9752" in normalized:
                score += 4
            if "\u77f3\u6cb9\u6ca5\u9752" in normalized:
                score -= 4
            if intent["wants_tack"]:
                if "\u9ecf\u5c42" in normalized or "\u7c98\u5c42" in normalized:
                    score += 8
                if "\u900f\u5c42" in normalized:
                    score -= 8
            elif intent["wants_prime"]:
                if "\u900f\u5c42" in normalized:
                    score += 8
                if "\u9ecf\u5c42" in normalized or "\u7c98\u5c42" in normalized:
                    score -= 8
                if intent["semi_rigid"] and "\u534a\u521a\u6027\u57fa\u5c42" in normalized:
                    score += 6
                elif intent["semi_rigid"] and "\u7c92\u6599\u57fa\u5c42" in normalized:
                    score -= 3

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "score": score,
            })
            if score < 12:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {"item_text": item_text, "intent": intent, "inspected": inspected}, ltr_ranked

        _score, rank, candidate = best
        if candidate is ltr_ranked[0]:
            return True, "bitumen_layer_confirmed", {
                "item_text": item_text,
                "intent": intent,
                "rescued_rank": rank,
                "rescued_quota_id": str(candidate.get("quota_id") or ""),
                "rescued_text": cls._candidate_query_text(candidate),
                "inspected": inspected,
            }, ltr_ranked

        rescued = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        return True, "bitumen_layer_rescued", {
            "item_text": item_text,
            "intent": intent,
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, rescued

    @classmethod
    def _apply_water_stabilized_paver_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        intent = cls._water_stabilized_paver_intent(item_text)
        if not intent["water_stabilized"] or not intent["paver"]:
            return False, "", {"item_text": item_text, "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "")
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            score = 0
            if candidate_prefix != "2":
                score -= 20
            if "\u6c34\u6ce5\u7a33\u5b9a\u788e\u77f3" in normalized:
                score += 4
            if "\u644a\u94fa\u673a\u644a\u94fa" in normalized:
                score += 10
            if "\u4eba\u94fa" in normalized:
                score -= 8
            if "\u6bcf\u51cf" in normalized or "\u6bcf\u589e\u51cf" in normalized:
                score -= 6
            elif intent["thick_layer"] and "\u539a20cm" in normalized:
                score += 4

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "score": score,
            })
            if score < 12:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {"item_text": item_text, "intent": intent, "inspected": inspected}, ltr_ranked

        _score, rank, candidate = best
        ranked = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        reason = "water_stabilized_paver_confirmed" if candidate is ltr_ranked[0] else "water_stabilized_paver_rescued"
        return True, reason, {
            "item_text": item_text,
            "intent": intent,
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, ranked

    @classmethod
    def _apply_curb_stone_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        specialty = (
            item.get("specialty")
            or item.get("_resolved_specialty")
            or (context or {}).get("specialty")
            or ""
        )
        intent = cls._curb_stone_intent(item_text, str(specialty))
        if not intent["municipal"] or not intent["install"] or not intent["wants_curb"] or intent["demolition"]:
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        top = ltr_ranked[0]
        top_text = cls._candidate_query_text(top).replace(" ", "")
        top_prefix = cls._quota_major_prefix(top.get("quota_id"))
        top_conflict = any(
            term in top_text
            for term in (
                "\u62c6\u9664",
                "\u7ffb\u6316",
                "\u6a21\u677f",
                "\u8fb9\u5899",
                "\u6599\u77f3",
            )
        )
        top_aligned = bool(
            top_prefix == "2"
            and not top_conflict
            and (
                (intent["wants_side"] and "\u4fa7\u77f3" in top_text)
                or (intent["wants_flat"] and "\u5e73\u77f3" in top_text)
            )
        )
        if top_aligned:
            return True, "curb_stone_confirmed", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "top_quota_id": str(top.get("quota_id") or ""),
                "top_text": cls._candidate_query_text(top),
                "top_prefix": top_prefix,
            }, ltr_ranked

        if not top_conflict:
            return False, "", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "top_text": top_text,
            }, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[1:8], start=2):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "")
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            score = 0
            if candidate_prefix != "2":
                score -= 20
            candidate_conflict = any(
                term in normalized
                for term in (
                    "\u62c6\u9664",
                    "\u7ffb\u6316",
                    "\u6a21\u677f",
                    "\u8fb9\u5899",
                    "\u6599\u77f3",
                )
            )
            if candidate_conflict:
                score -= 10
            if "\u4fa7\u77f3\u77f3\u8d28" in normalized:
                score += 8
                if intent["wants_side"]:
                    score += 4
                if intent["wants_flat"] and not intent["wants_side"]:
                    score -= 4
            elif "\u4fa7\u77f3" in normalized and not candidate_conflict:
                score += 6
                if intent["wants_side"]:
                    score += 4
                if intent["wants_flat"] and not intent["wants_side"]:
                    score -= 4
            if "\u5e73\u77f3\u77f3\u8d28" in normalized or "\u4fa7\u3001\u5e73\u77f3\u77f3\u8d28" in normalized:
                score += 8
                if intent["wants_flat"]:
                    score += 4
                if intent["wants_side"] and not intent["wants_flat"]:
                    score -= 3
            elif "\u5e73\u77f3" in normalized and not candidate_conflict:
                score += 6
                if intent["wants_flat"]:
                    score += 4
                if intent["wants_side"] and not intent["wants_flat"]:
                    score -= 3
            if "\u6df7\u51dd\u571f\u57ab\u5c42" in normalized or "\u7802\u6d46\u7c98\u7ed3\u5c42" in normalized:
                score -= 5

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "candidate_conflict": candidate_conflict,
                "score": score,
            })
            if score < 10:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "top_text": top_text,
                "inspected": inspected,
            }, ltr_ranked

        _score, rank, candidate = best
        rescued = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        return True, "curb_stone_rescued", {
            "item_text": item_text,
            "specialty": str(specialty),
            "intent": intent,
            "top_quota_id": str(top.get("quota_id") or ""),
            "top_text": cls._candidate_query_text(top),
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, rescued

    @classmethod
    def _apply_portal_frame_sign_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        specialty = (
            item.get("specialty")
            or item.get("_resolved_specialty")
            or (context or {}).get("specialty")
            or ""
        )
        intent = cls._portal_frame_sign_intent(item_text, str(specialty))
        if (
            not intent["municipal"]
            or not intent["wants_portal_frame"]
            or intent["cantilever_only"]
            or intent["support_only"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "")
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            conflict = any(term in normalized for term in (
                "\u94a2\u652f\u6491",
                "\u8f66\u67b6",
                "\u94a2\u7ba1\u62f1",
                "\u652f\u6491\u67b6",
                "\u91d1\u5c5e\u95e8\u7a97",
                "\u680f\u6746",
                "\u5899\u9762\u62c6\u9664",
            ))
            score = 0
            if candidate_prefix == "2":
                score += 10
            else:
                score -= 22
            if "\u60ac\u81c2\u5f0f\u3001\u95e8\u5f0f\u67b6" in normalized and "\u95e8\u5f0f\u67b6" in normalized:
                score += 12
            elif "\u95e8\u5f0f\u67b6" in normalized:
                score += 8
            if "\u60ac\u81c2\u5f0fT\u6746" in normalized or "\u60ac\u81c2\u5f0f\uff34\u6746" in normalized:
                score -= 8
            if "\u62c6\u9664" in normalized and "\u95e8\u5f0f\u67b6" not in normalized:
                score -= 5
            if conflict:
                score -= 10

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "conflict": conflict,
                "score": score,
            })
            if score < 18:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "inspected": inspected,
            }, ltr_ranked

        _score, rank, candidate = best
        ranked = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        reason = (
            "portal_frame_sign_confirmed"
            if candidate is ltr_ranked[0]
            else "portal_frame_sign_rescued"
        )
        return True, reason, {
            "item_text": item_text,
            "specialty": str(specialty),
            "intent": intent,
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, ranked

    @classmethod
    def _apply_precast_laminated_slab_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        specialty = (
            item.get("specialty")
            or item.get("_resolved_specialty")
            or (context or {}).get("specialty")
            or ""
        )
        intent = cls._precast_laminated_slab_intent(item_text, str(specialty))
        if not intent["building"] or not intent["wants_laminated_slab"]:
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            wall_panel = any(term in normalized for term in ("\u5916\u5899\u9762\u677f", "\u5916\u5899", "\u5899\u677f", "PCF"))
            score = 0
            if candidate_prefix != "5":
                score -= 20
            if "\u88c5\u914d\u5f0f\u6df7\u51dd\u571f\u6784\u4ef6" in normalized:
                score += 3
            if "\u53e0\u5408\u677f" in normalized or "\u53e0\u5408\u697c\u677f" in normalized:
                score += 10
            if wall_panel:
                score -= 10
            if any(term in normalized for term in ("\u6881", "\u67f1", "\u7ba1\u7247")) and "\u53e0\u5408" not in normalized:
                score -= 4

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "wall_panel": wall_panel,
                "score": score,
            })
            if score < 10:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "inspected": inspected,
            }, ltr_ranked

        _score, rank, candidate = best
        ranked = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        reason = (
            "precast_laminated_slab_confirmed"
            if candidate is ltr_ranked[0]
            else "precast_laminated_slab_rescued"
        )
        return True, reason, {
            "item_text": item_text,
            "specialty": str(specialty),
            "intent": intent,
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, ranked

    @classmethod
    def _apply_concrete_foundation_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        specialty = (
            item.get("specialty")
            or item.get("_resolved_specialty")
            or (context or {}).get("specialty")
            or ""
        )
        intent = cls._concrete_foundation_intent(item_text, str(specialty))
        if (
            not intent["bridge"]
            or not intent["wants_concrete_foundation"]
            or not intent["explicit_concrete"]
            or intent["stone_foundation"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "")
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            conflict = any(term in normalized for term in ("\u5757\u77f3", "\u7247\u77f3", "\u6bdb\u77f3", "\u6a21\u677f", "\u57ab\u5c42"))
            score = 0
            if candidate_prefix != "3":
                score -= 20
            if "\u6df7\u51dd\u571f\u57fa\u7840\u6df7\u51dd\u571f" in normalized:
                score += 12
            elif "\u6df7\u51dd\u571f\u57fa\u7840" in normalized and "\u6df7\u51dd\u571f" in normalized:
                score += 8
            if conflict:
                score -= 8

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "conflict": conflict,
                "score": score,
            })
            if score < 10:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "inspected": inspected,
            }, ltr_ranked

        _score, rank, candidate = best
        ranked = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        reason = (
            "concrete_foundation_confirmed"
            if candidate is ltr_ranked[0]
            else "concrete_foundation_rescued"
        )
        return True, reason, {
            "item_text": item_text,
            "specialty": str(specialty),
            "intent": intent,
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, ranked

    @classmethod
    def _apply_foam_expansion_joint_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        specialty = (
            item.get("specialty")
            or item.get("_resolved_specialty")
            or (context or {}).get("specialty")
            or ""
        )
        intent = cls._foam_expansion_joint_intent(item_text, str(specialty))
        if not intent["waterproof"] or not intent["wants_joint"] or not intent["wants_foam"] or intent["oil_hemp"]:
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "")
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            conflict = any(term in normalized for term in ("\u6cb9\u6d78\u9ebb\u4e1d", "\u6ca5\u9752\u7802\u6d46", "\u7f38\u7816"))
            score = 0
            if candidate_prefix != "9":
                score -= 20
            if "\u5d4c\u586b\u7f1d" in normalized:
                score += 3
            if "\u6ce1\u6cab\u5851\u6599\u586b\u585e" in normalized:
                score += 10
            elif "\u6ce1\u6cab\u5851\u6599" in normalized:
                score += 7
            if "\u5e73\u9762" in normalized:
                score += 2
            if conflict:
                score -= 8

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "conflict": conflict,
                "score": score,
            })
            if score < 10:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "inspected": inspected,
            }, ltr_ranked

        _score, rank, candidate = best
        ranked = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        reason = (
            "foam_expansion_joint_confirmed"
            if candidate is ltr_ranked[0]
            else "foam_expansion_joint_rescued"
        )
        return True, reason, {
            "item_text": item_text,
            "specialty": str(specialty),
            "intent": intent,
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, ranked

    @classmethod
    def _apply_self_adhesive_polymer_membrane_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        specialty = (
            item.get("specialty")
            or item.get("_resolved_specialty")
            or (context or {}).get("specialty")
            or ""
        )
        intent = cls._self_adhesive_polymer_membrane_intent(item_text, str(specialty))
        if (
            not intent["waterproof"]
            or not intent["wants_membrane"]
            or not intent["wants_self_adhesive"]
            or not intent["wants_polymer"]
            or intent["modified_bitumen"]
            or not intent["wants_horizontal"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "")
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            conflict = "\u6539\u6027\u6ca5\u9752" in normalized or "\u6ca5\u9752" in normalized or "\u80f6\u7c98\u6cd5" in normalized
            score = 0
            if candidate_prefix != "9":
                score -= 20
            if "\u9ad8\u5206\u5b50\u5377\u6750" in normalized:
                score += 8
            elif "\u9ad8\u5206\u5b50" in normalized and "\u5377\u6750" in normalized:
                score += 6
            if "\u81ea\u7c98\u6cd5" in normalized:
                score += 5
            if "\u5e73\u9762" in normalized:
                score += 3
            if "\u7acb\u9762" in normalized:
                score -= 3
            if "\u4e00\u5c42" in normalized:
                score += 1
            if "\u6bcf\u589e" in normalized:
                score -= 3
            if conflict:
                score -= 8

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "conflict": conflict,
                "score": score,
            })
            if score < 10:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "inspected": inspected,
            }, ltr_ranked

        _score, rank, candidate = best
        ranked = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        reason = (
            "self_adhesive_polymer_membrane_confirmed"
            if candidate is ltr_ranked[0]
            else "self_adhesive_polymer_membrane_rescued"
        )
        return True, reason, {
            "item_text": item_text,
            "specialty": str(specialty),
            "intent": intent,
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, ranked

    @classmethod
    def _apply_cementitious_crystalline_waterproof_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        specialty = (
            item.get("specialty")
            or item.get("_resolved_specialty")
            or (context or {}).get("specialty")
            or ""
        )
        intent = cls._cementitious_crystalline_waterproof_intent(item_text, str(specialty))
        if (
            not intent["waterproof"]
            or not intent["wants_crystalline"]
            or not intent["wants_coating"]
            or intent["polymer_cement"]
            or intent["other_material"]
            or not intent["wants_horizontal"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "")
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            conflict = any(term in normalized for term in ("\u6539\u6027\u6ca5\u9752", "\u6ca5\u9752", "\u805a\u5408\u7269\u6c34\u6ce5", "\u805a\u6c28\u916f"))
            score = 0
            if candidate_prefix != "9":
                score -= 20
            if "\u6c34\u6ce5\u57fa\u6e17\u900f\u7ed3\u6676\u578b\u9632\u6c34\u6d82\u6599" in normalized:
                score += 10
            elif "\u6c34\u6ce5\u57fa" in normalized and "\u6e17\u900f\u7ed3\u6676" in normalized:
                score += 8
            if "\u539a\u5ea61.0MM" in normalized or "\u539a\u5ea61MM" in normalized:
                score += 4
            if "\u5e73\u9762" in normalized:
                score += 3
            if "\u7acb\u9762" in normalized:
                score -= 3
            if "\u6bcf\u589e" in normalized or "\u6bcf\u51cf" in normalized:
                score -= 5
            if conflict:
                score -= 8

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "conflict": conflict,
                "score": score,
            })
            if score < 12:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "inspected": inspected,
            }, ltr_ranked

        _score, rank, candidate = best
        ranked = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        reason = (
            "cementitious_crystalline_waterproof_confirmed"
            if candidate is ltr_ranked[0]
            else "cementitious_crystalline_waterproof_rescued"
        )
        return True, reason, {
            "item_text": item_text,
            "specialty": str(specialty),
            "intent": intent,
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, ranked

    @classmethod
    def _apply_polymer_cement_waterproof_coating_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        specialty = (
            item.get("specialty")
            or item.get("_resolved_specialty")
            or (context or {}).get("specialty")
            or ""
        )
        intent = cls._polymer_cement_waterproof_coating_intent(item_text, str(specialty))
        if (
            not intent["waterproof"]
            or not intent["wants_polymer_cement"]
            or not intent["wants_coating"]
            or not intent["horizontal_anchor"]
            or intent["vertical_only"]
            or intent["increment_only"]
            or intent["other_material"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            conflict = any(term in normalized for term in (
                "\u6bcf\u589e",
                "\u6bcf\u51cf",
                "\u6539\u6027\u6ca5\u9752",
                "\u6ca5\u9752",
                "\u805a\u6c28\u916f",
                "\u6e17\u900f\u7ed3\u6676",
            ))
            score = 0
            if candidate_prefix == "9":
                score += 10
            else:
                score -= 20
            if "\u805a\u5408\u7269\u6c34\u6ce5\u9632\u6c34\u6d82\u6599" in normalized:
                score += 9
            elif "\u805a\u5408\u7269\u6c34\u6ce5" in normalized and "\u6d82\u6599" in normalized:
                score += 7
            if "\u539a\u5ea61.2MM" in normalized or "\u539a\u5ea61.2" in normalized:
                score += 5
            if "\u5e73\u9762" in normalized:
                score += 5
            if "\u7acb\u9762" in normalized:
                score -= 6
            if conflict:
                score -= 8

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "conflict": conflict,
                "score": score,
            })
            if score < 22:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "inspected": inspected,
            }, ltr_ranked

        _score, rank, candidate = best
        ranked = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        reason = (
            "polymer_cement_waterproof_coating_confirmed"
            if candidate is ltr_ranked[0]
            else "polymer_cement_waterproof_coating_rescued"
        )
        return True, reason, {
            "item_text": item_text,
            "specialty": str(specialty),
            "intent": intent,
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, ranked

    @classmethod
    def _apply_embedded_iron_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        specialty = (
            item.get("specialty")
            or item.get("_resolved_specialty")
            or (context or {}).get("specialty")
            or ""
        )
        intent = cls._embedded_iron_intent(item_text, str(specialty))
        if (
            not (intent["building"] or intent["road"])
            or not intent["wants_embedded_iron"]
            or intent["bolt_only"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        wants_above = bool(intent["above_25kg"])
        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            has_embedded_iron = "\u9884\u57cb\u94c1\u4ef6" in normalized
            candidate_above = "25KG/\u5757\u4ee5\u4e0a" in normalized or "25KG\u4ee5\u4e0a" in normalized
            candidate_within = "25KG/\u5757\u4ee5\u5185" in normalized or "25KG\u4ee5\u5185" in normalized
            conflict = any(term in normalized for term in (
                "\u87ba\u6813",
                "\u9632\u9508\u6f06",
                "\u6b62\u6c34",
                "\u951a\u56fa\u4ef6",
                "\u4e00\u822c\u94c1\u6784\u4ef6",
            ))
            score = 0
            if intent["road"]:
                if candidate_prefix == "1":
                    score += 10
                else:
                    score -= 18
                if has_embedded_iron:
                    score += 8
                if "\u94c1\u4ef6\u5236\u4f5c" in normalized and "\u5b89\u88c5" in normalized:
                    score += 8
                if candidate_above or candidate_within:
                    score -= 6
            else:
                if candidate_prefix != "5":
                    score -= 25
                if has_embedded_iron:
                    score += 10
                if wants_above:
                    if candidate_above:
                        score += 8
                    if candidate_within:
                        score -= 7
                else:
                    if candidate_within:
                        score += 8
                    if candidate_above:
                        score -= 4
                if intent["has_plate_anchor"]:
                    score += 1
            if conflict:
                score -= 8

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "conflict": conflict,
                "score": score,
            })
            if score < 14:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "inspected": inspected,
            }, ltr_ranked

        _score, rank, candidate = best
        ranked = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        reason = (
            "embedded_iron_confirmed"
            if candidate is ltr_ranked[0]
            else "embedded_iron_rescued"
        )
        return True, reason, {
            "item_text": item_text,
            "specialty": str(specialty),
            "intent": intent,
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, ranked

    @classmethod
    def _apply_postcast_rebar_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        specialty = (
            item.get("specialty")
            or item.get("_resolved_specialty")
            or (context or {}).get("specialty")
            or ""
        )
        intent = cls._postcast_rebar_intent(item_text, str(specialty))
        if (
            not intent["building"]
            or not intent["wants_postcast"]
            or not intent["wants_deformed"]
            or not intent["diameter_le_10"]
            or intent["hoop"]
            or intent["round_steel"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            conflict = any(term in normalized for term in (
                "\u907f\u96f7",
                "\u73b0\u6d47\u6784\u4ef6",
                "HPB300",
                "\u5706\u94a2\u7b4b",
                "\u7b8d\u7b4b",
                "HRB400\u4ee5\u4e0a",
                "\u690d\u7b4b",
            ))
            score = 0
            if candidate_prefix != "5":
                score -= 25
            if "\u540e\u6d47\u6df7\u51dd\u571f" in normalized:
                score += 5
            if "\u5e26\u808b\u94a2\u7b4b" in normalized:
                score += 4
            if "HRB400\u4ee5\u5185" in normalized:
                score += 4
            if "\u76f4\u5f8410MM\u4ee5\u5185" in normalized:
                score += 8
            if "\u76f4\u5f8425MM\u4ee5\u5185" in normalized or "\u76f4\u5f8418MM\u4ee5\u5185" in normalized:
                score -= 5
            if conflict:
                score -= 8

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "conflict": conflict,
                "score": score,
            })
            if score < 16:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "inspected": inspected,
            }, ltr_ranked

        _score, rank, candidate = best
        ranked = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        reason = (
            "postcast_rebar_confirmed"
            if candidate is ltr_ranked[0]
            else "postcast_rebar_rescued"
        )
        return True, reason, {
            "item_text": item_text,
            "specialty": str(specialty),
            "intent": intent,
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, ranked

    @classmethod
    def _apply_manhole_surround_backfill_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        specialty = (
            item.get("specialty")
            or item.get("_resolved_specialty")
            or (context or {}).get("specialty")
            or ""
        )
        intent = cls._manhole_surround_backfill_intent(item_text, str(specialty))
        if (
            not intent["drainage"]
            or not intent["wants_manhole_surround"]
            or not intent["wants_gravel"]
            or intent["artificial_grade"]
            or intent["bedding"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "")
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            conflict = any(term in normalized for term in (
                "\u4eba\u5de5\u7ea7\u914d",
                "\u57ab\u5c42",
                "\u4e95\u76d6",
                "\u7816\u780c",
                "\u6df7\u51dd\u571f",
                "\u4ef0\u62f1",
            ))
            score = 0
            if candidate_prefix != "6":
                score -= 25
            if "\u6c9f\u69fd\u56de\u586b" in normalized:
                score += 5
            if "\u7802\u783e\u77f3" in normalized:
                score += 5
            if "\u5929\u7136\u7ea7\u914d" in normalized:
                score += 8
            if conflict:
                score -= 8

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "conflict": conflict,
                "score": score,
            })
            if score < 16:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "inspected": inspected,
            }, ltr_ranked

        _score, rank, candidate = best
        ranked = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        reason = (
            "manhole_surround_backfill_confirmed"
            if candidate is ltr_ranked[0]
            else "manhole_surround_backfill_rescued"
        )
        return True, reason, {
            "item_text": item_text,
            "specialty": str(specialty),
            "intent": intent,
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, ranked

    @classmethod
    def _apply_sinking_well_bottom_slab_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        specialty = (
            item.get("specialty")
            or item.get("_resolved_specialty")
            or (context or {}).get("specialty")
            or ""
        )
        intent = cls._sinking_well_bottom_slab_intent(item_text, str(specialty))
        if (
            not intent["drainage"]
            or not intent["wants_sinking_well"]
            or not intent["wants_bottom_slab"]
            or not intent["concrete"]
            or intent["bedding"]
            or intent["template"]
            or intent["cover"]
            or intent["platform"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            conflict = any(term in normalized for term in (
                "\u57ab\u5c42",
                "\u6a21\u677f",
                "\u4e95\u76d6",
                "\u5730\u4e0b\u7ed3\u6784\u5e73\u53f0",
                "\u6846\u67b6",
            ))
            score = 0
            if candidate_prefix == "6":
                score += 12
            else:
                score -= 22
            if "\u6c89\u4e95\u5236\u4f5c" in normalized and "\u5e95\u677f" in normalized:
                score += 10
            elif "\u6c89\u4e95" in normalized and "\u5e95\u677f" in normalized:
                score += 7
            elif "\u5e95\u677f\u6df7\u51dd\u571f" in normalized:
                score += 2
            if "\u539a\u5ea650CM\u4ee5\u5185" in normalized or "\u539a\u5ea650CM\u5185" in normalized:
                score += 5
            if "\u539a\u5ea650CM\u5916" in normalized or "\u539a\u5ea650CM\u4ee5\u5916" in normalized:
                score += 5 if intent["over_50"] else -8
            if intent["within_50"] and ("\u539a\u5ea650CM\u5916" in normalized or "\u539a\u5ea650CM\u4ee5\u5916" in normalized):
                score -= 8
            if conflict:
                score -= 8

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "conflict": conflict,
                "score": score,
            })
            if score < 18:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "inspected": inspected,
            }, ltr_ranked

        _score, rank, candidate = best
        ranked = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        reason = (
            "sinking_well_bottom_slab_confirmed"
            if candidate is ltr_ranked[0]
            else "sinking_well_bottom_slab_rescued"
        )
        return True, reason, {
            "item_text": item_text,
            "specialty": str(specialty),
            "intent": intent,
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, ranked

    @classmethod
    def _apply_surplus_soil_disposal_rescue(
        cls,
        item: dict,
        ltr_ranked: list[dict],
        context: dict | None = None,
    ) -> tuple[bool, str, dict, list[dict]]:
        if len(ltr_ranked) < 2:
            return False, "", {}, ltr_ranked

        item_text = cls._item_query_text(item, context)
        specialty = (
            item.get("specialty")
            or item.get("_resolved_specialty")
            or (context or {}).get("specialty")
            or ""
        )
        intent = cls._surplus_soil_disposal_intent(item_text, str(specialty))
        if (
            not intent["road"]
            or not intent["wants_surplus"]
            or not intent["wants_haul"]
            or not intent["soil_context"]
            or intent["stone_only"]
            or intent["backfill"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "")
            normalized_upper = normalized.upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            conflict = any(term in normalized for term in ("\u56de\u586b", "\u501f\u571f", "\u77f3\u78b4", "\u77f3\u6e23", "\u8fd0\u77f3"))
            score = 0
            if candidate_prefix == "1":
                score += 10
            else:
                score -= 18
            if "\u81ea\u5378\u6c7d\u8f66\u8fd0\u571f\u65b9" in normalized:
                score += 10
            elif "\u8fd0\u571f\u65b9" in normalized:
                score += 6
            if "\u8fd0\u8ddd1KM\u4ee5\u5185" in normalized_upper or "\u8fd0\u8ddd1000M\u4ee5\u5185" in normalized_upper:
                score += 4
            if "\u6bcf\u589e" in normalized or "\u6bcf\u589e\u52a0" in normalized:
                score -= 5
            if "\u4eba\u5de5" in normalized:
                score -= 3
            if conflict:
                score -= 8

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "conflict": conflict,
                "score": score,
            })
            if score < 16:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "inspected": inspected,
            }, ltr_ranked

        _score, rank, candidate = best
        ranked = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        reason = (
            "surplus_soil_disposal_confirmed"
            if candidate is ltr_ranked[0]
            else "surplus_soil_disposal_rescued"
        )
        return True, reason, {
            "item_text": item_text,
            "specialty": str(specialty),
            "intent": intent,
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, ranked

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
        slab_blocked, slab_reason, slab_details, slab_ranked = cls._apply_precast_laminated_slab_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["precast_laminated_slab_rescue"] = {
            "blocked": slab_blocked,
            "reason": slab_reason,
            "details": slab_details,
        }
        if slab_blocked:
            rescued_id = str(slab_ranked[0].get("quota_id", "") or "") if slab_ranked else ""
            slab_ranked[0]["_rank_score_source"] = "manual"
            slab_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = slab_reason
            meta["final_top1_id"] = rescued_id
            return slab_ranked, meta

        foundation_blocked, foundation_reason, foundation_details, foundation_ranked = cls._apply_concrete_foundation_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["concrete_foundation_rescue"] = {
            "blocked": foundation_blocked,
            "reason": foundation_reason,
            "details": foundation_details,
        }
        if foundation_blocked:
            rescued_id = str(foundation_ranked[0].get("quota_id", "") or "") if foundation_ranked else ""
            foundation_ranked[0]["_rank_score_source"] = "manual"
            foundation_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = foundation_reason
            meta["final_top1_id"] = rescued_id
            return foundation_ranked, meta

        foam_blocked, foam_reason, foam_details, foam_ranked = cls._apply_foam_expansion_joint_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["foam_expansion_joint_rescue"] = {
            "blocked": foam_blocked,
            "reason": foam_reason,
            "details": foam_details,
        }
        if foam_blocked:
            rescued_id = str(foam_ranked[0].get("quota_id", "") or "") if foam_ranked else ""
            foam_ranked[0]["_rank_score_source"] = "manual"
            foam_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = foam_reason
            meta["final_top1_id"] = rescued_id
            return foam_ranked, meta

        membrane_blocked, membrane_reason, membrane_details, membrane_ranked = cls._apply_self_adhesive_polymer_membrane_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["self_adhesive_polymer_membrane_rescue"] = {
            "blocked": membrane_blocked,
            "reason": membrane_reason,
            "details": membrane_details,
        }
        if membrane_blocked:
            rescued_id = str(membrane_ranked[0].get("quota_id", "") or "") if membrane_ranked else ""
            membrane_ranked[0]["_rank_score_source"] = "manual"
            membrane_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = membrane_reason
            meta["final_top1_id"] = rescued_id
            return membrane_ranked, meta

        polymer_cement_blocked, polymer_cement_reason, polymer_cement_details, polymer_cement_ranked = cls._apply_polymer_cement_waterproof_coating_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["polymer_cement_waterproof_coating_rescue"] = {
            "blocked": polymer_cement_blocked,
            "reason": polymer_cement_reason,
            "details": polymer_cement_details,
        }
        if polymer_cement_blocked:
            rescued_id = str(polymer_cement_ranked[0].get("quota_id", "") or "") if polymer_cement_ranked else ""
            polymer_cement_ranked[0]["_rank_score_source"] = "manual"
            polymer_cement_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = polymer_cement_reason
            meta["final_top1_id"] = rescued_id
            return polymer_cement_ranked, meta

        crystalline_blocked, crystalline_reason, crystalline_details, crystalline_ranked = cls._apply_cementitious_crystalline_waterproof_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["cementitious_crystalline_waterproof_rescue"] = {
            "blocked": crystalline_blocked,
            "reason": crystalline_reason,
            "details": crystalline_details,
        }
        if crystalline_blocked:
            rescued_id = str(crystalline_ranked[0].get("quota_id", "") or "") if crystalline_ranked else ""
            crystalline_ranked[0]["_rank_score_source"] = "manual"
            crystalline_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = crystalline_reason
            meta["final_top1_id"] = rescued_id
            return crystalline_ranked, meta

        embedded_blocked, embedded_reason, embedded_details, embedded_ranked = cls._apply_embedded_iron_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["embedded_iron_rescue"] = {
            "blocked": embedded_blocked,
            "reason": embedded_reason,
            "details": embedded_details,
        }
        if embedded_blocked:
            rescued_id = str(embedded_ranked[0].get("quota_id", "") or "") if embedded_ranked else ""
            embedded_ranked[0]["_rank_score_source"] = "manual"
            embedded_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = embedded_reason
            meta["final_top1_id"] = rescued_id
            return embedded_ranked, meta

        rebar_blocked, rebar_reason, rebar_details, rebar_ranked = cls._apply_postcast_rebar_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["postcast_rebar_rescue"] = {
            "blocked": rebar_blocked,
            "reason": rebar_reason,
            "details": rebar_details,
        }
        if rebar_blocked:
            rescued_id = str(rebar_ranked[0].get("quota_id", "") or "") if rebar_ranked else ""
            rebar_ranked[0]["_rank_score_source"] = "manual"
            rebar_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = rebar_reason
            meta["final_top1_id"] = rescued_id
            return rebar_ranked, meta

        manhole_backfill_blocked, manhole_backfill_reason, manhole_backfill_details, manhole_backfill_ranked = cls._apply_manhole_surround_backfill_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["manhole_surround_backfill_rescue"] = {
            "blocked": manhole_backfill_blocked,
            "reason": manhole_backfill_reason,
            "details": manhole_backfill_details,
        }
        if manhole_backfill_blocked:
            rescued_id = str(manhole_backfill_ranked[0].get("quota_id", "") or "") if manhole_backfill_ranked else ""
            manhole_backfill_ranked[0]["_rank_score_source"] = "manual"
            manhole_backfill_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = manhole_backfill_reason
            meta["final_top1_id"] = rescued_id
            return manhole_backfill_ranked, meta

        sinking_well_blocked, sinking_well_reason, sinking_well_details, sinking_well_ranked = cls._apply_sinking_well_bottom_slab_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["sinking_well_bottom_slab_rescue"] = {
            "blocked": sinking_well_blocked,
            "reason": sinking_well_reason,
            "details": sinking_well_details,
        }
        if sinking_well_blocked:
            rescued_id = str(sinking_well_ranked[0].get("quota_id", "") or "") if sinking_well_ranked else ""
            sinking_well_ranked[0]["_rank_score_source"] = "manual"
            sinking_well_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = sinking_well_reason
            meta["final_top1_id"] = rescued_id
            return sinking_well_ranked, meta

        surplus_soil_blocked, surplus_soil_reason, surplus_soil_details, surplus_soil_ranked = cls._apply_surplus_soil_disposal_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["surplus_soil_disposal_rescue"] = {
            "blocked": surplus_soil_blocked,
            "reason": surplus_soil_reason,
            "details": surplus_soil_details,
        }
        if surplus_soil_blocked:
            rescued_id = str(surplus_soil_ranked[0].get("quota_id", "") or "") if surplus_soil_ranked else ""
            surplus_soil_ranked[0]["_rank_score_source"] = "manual"
            surplus_soil_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = surplus_soil_reason
            meta["final_top1_id"] = rescued_id
            return surplus_soil_ranked, meta

        curb_blocked, curb_reason, curb_details, curb_ranked = cls._apply_curb_stone_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["curb_stone_rescue"] = {
            "blocked": curb_blocked,
            "reason": curb_reason,
            "details": curb_details,
        }
        if curb_blocked:
            rescued_id = str(curb_ranked[0].get("quota_id", "") or "") if curb_ranked else ""
            curb_ranked[0]["_rank_score_source"] = "manual"
            curb_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = curb_reason
            meta["final_top1_id"] = rescued_id
            return curb_ranked, meta

        portal_frame_blocked, portal_frame_reason, portal_frame_details, portal_frame_ranked = cls._apply_portal_frame_sign_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["portal_frame_sign_rescue"] = {
            "blocked": portal_frame_blocked,
            "reason": portal_frame_reason,
            "details": portal_frame_details,
        }
        if portal_frame_blocked:
            rescued_id = str(portal_frame_ranked[0].get("quota_id", "") or "") if portal_frame_ranked else ""
            portal_frame_ranked[0]["_rank_score_source"] = "manual"
            portal_frame_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = portal_frame_reason
            meta["final_top1_id"] = rescued_id
            return portal_frame_ranked, meta

        paver_blocked, paver_reason, paver_details, paver_ranked = cls._apply_water_stabilized_paver_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["water_stabilized_paver_rescue"] = {
            "blocked": paver_blocked,
            "reason": paver_reason,
            "details": paver_details,
        }
        if paver_blocked:
            rescued_id = str(paver_ranked[0].get("quota_id", "") or "") if paver_ranked else ""
            paver_ranked[0]["_rank_score_source"] = "manual"
            paver_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = paver_reason
            meta["final_top1_id"] = rescued_id
            return paver_ranked, meta

        bitumen_blocked, bitumen_reason, bitumen_details, bitumen_ranked = cls._apply_bitumen_layer_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["bitumen_layer_rescue"] = {
            "blocked": bitumen_blocked,
            "reason": bitumen_reason,
            "details": bitumen_details,
        }
        if bitumen_blocked:
            rescued_id = str(bitumen_ranked[0].get("quota_id", "") or "") if bitumen_ranked else ""
            bitumen_ranked[0]["_rank_score_source"] = "manual"
            bitumen_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = bitumen_reason
            meta["final_top1_id"] = rescued_id
            return bitumen_ranked, meta

        rescue_blocked, rescue_reason, rescue_details, rescue_ranked = cls._apply_surface_orientation_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["surface_orientation_rescue"] = {
            "blocked": rescue_blocked,
            "reason": rescue_reason,
            "details": rescue_details,
        }
        if rescue_blocked:
            rescued_id = str(rescue_ranked[0].get("quota_id", "") or "") if rescue_ranked else ""
            rescue_ranked[0]["_rank_score_source"] = "manual"
            rescue_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = rescue_reason
            meta["final_top1_id"] = rescued_id
            return rescue_ranked, meta

        if not incumbent_id or not challenger_id or incumbent_id == challenger_id:
            meta["action"] = "no_change"
            meta["reason"] = "same_top1"
            return ltr_ranked, meta

        anchor_score, anchor_details = cls._compute_ltr_anchor(item, incumbent, context)
        meta["anchor_score"] = anchor_score
        meta["anchor_details"] = anchor_details
        semantic_allow, semantic_reason, semantic_details = cls._detect_explicit_semantic_advantage(
            item,
            incumbent,
            challenger,
            context,
        )
        meta["semantic_guard"] = {
            "allow_ltr": semantic_allow,
            "reason": semantic_reason,
            "details": semantic_details,
        }
        if semantic_allow:
            meta["action"] = "allowed"
            meta["reason"] = semantic_reason
            return ltr_ranked, meta

        snapshot_guard_blocked, snapshot_reason, snapshot_details = cls._apply_snapshot_struct_guard(
            incumbent,
            challenger,
        )
        orientation_guard_blocked, orientation_reason, orientation_details = cls._apply_surface_orientation_guard(
            item,
            incumbent,
            challenger,
            context,
        )
        stability_guard_blocked, stability_reason, stability_details = cls._apply_pre_ltr_stability_guard(
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
        meta["surface_orientation_guard"] = {
            "blocked": orientation_guard_blocked,
            "reason": orientation_reason,
            "details": orientation_details,
        }
        meta["pre_ltr_stability_guard"] = {
            "blocked": stability_guard_blocked,
            "reason": stability_reason,
            "details": stability_details,
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

        if orientation_guard_blocked:
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
            meta["reason"] = orientation_reason
            meta["final_top1_id"] = incumbent_id
            return guarded, meta

        if stability_guard_blocked:
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
            meta["reason"] = stability_reason
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
