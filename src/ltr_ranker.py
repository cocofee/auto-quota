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
    def _traffic_sign_shape_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_sign = "\u6807\u5fd7\u724c" in normalized or "\u6807\u5fd7\u677f" in normalized
        wants_triangle = "\u25b3" in normalized or "\u4e09\u89d2\u5f62" in normalized
        triangle_size = ""
        match = re.search(r"\u25b3\s*(\d+(?:\.\d+)?)", normalized)
        if not match:
            match = re.search(r"\u4e09\u89d2\u5f62(?:\([^)]*\))?\u25b3?\s*(\d+(?:\.\d+)?)", normalized)
        if match:
            triangle_size = match.group(1)
        return {
            "municipal": specialty_text in {"C2", "2"},
            "wants_sign": wants_sign,
            "wants_triangle": wants_triangle,
            "triangle_size": triangle_size,
        }

    @staticmethod
    def _geotextile_tape_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_geosynthetic = any(term in normalized for term in (
            "\u571f\u5de5\u5408\u6210\u6750\u6599",
            "\u571f\u5de5\u5e03",
            "\u5e94\u529b\u5438\u6536\u8d34",
            "\u9632\u88c2\u8d34",
            "\u6297\u88c2\u8d34",
            "\u6297\u529b\u8d34",
        ))
        wants_tape = any(term in normalized for term in (
            "\u5e94\u529b\u5438\u6536\u8d34",
            "\u9632\u88c2\u8d34",
            "\u6297\u88c2\u8d34",
            "\u6297\u529b\u8d34",
            "\u8d34\u7f1d",
        ))
        explicit_laying = any(term in normalized for term in ("\u5e73\u94fa", "\u659c\u94fa"))
        return {
            "municipal": specialty_text in {"C2", "2"},
            "wants_geosynthetic": wants_geosynthetic,
            "wants_tape": wants_tape,
            "explicit_laying": explicit_laying,
        }

    @staticmethod
    def _road_saw_cut_joint_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_road = "\u8def\u9762" in normalized
        wants_cut = any(term in normalized for term in (
            "\u5272\u636e\u7f1d",
            "\u5272\u952f\u7f1d",
            "\u952f\u7f1d",
            "\u5207\u7f1d",
        ))
        deformation_joint = any(term in normalized for term in (
            "\u53d8\u5f62\u7f1d",
            "\u4f38\u7f1d",
            "\u6da8\u7f1d",
            "\u80c0\u7f1d",
            "\u739b\u8e44\u8102",
            "\u586b\u704c\u7f1d",
            "\u4f38\u7f29\u7f1d",
        ))
        return {
            "municipal": specialty_text in {"C2", "2"},
            "wants_road": wants_road,
            "wants_cut": wants_cut,
            "deformation_joint": deformation_joint,
        }

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
    def _shotcrete_slope_base_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_shotcrete = "\u55b7\u5c04\u6df7\u51dd\u571f" in normalized
        wants_slope = "\u62a4\u5761" in normalized
        slope_within_60 = any(term in normalized for term in (
            "\u5761\u5ea660\u00b0\u4ee5\u5185",
            "\u5761\u5ea660\u4ee5\u5185",
            "\u5761\u5ea6<60",
            "\u5761\u5ea6\u226460",
            "\u5761\u5ea6<=60",
        ))
        tunnel_or_spray_conflict = any(term in normalized for term in (
            "\u6d1e\u5185",
            "\u96a7\u9053",
            "\u62f1\u90e8",
            "\u7ba1\u9053",
            "\u5185\u55b7\u6d82",
        ))
        return {
            "municipal": specialty_text in {"C2", "2"},
            "wants_shotcrete": wants_shotcrete,
            "wants_slope": wants_slope,
            "slope_within_60": slope_within_60,
            "tunnel_or_spray_conflict": tunnel_or_spray_conflict,
        }

    @staticmethod
    def _road_milling_base_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_milling = "\u94e3\u5228\u8def\u9762" in normalized or "\u94e3\u5228" in normalized
        demolition_only = "\u62c6\u9664" in normalized and not wants_milling
        return {
            "demolition": specialty_text in {"C1", "1"},
            "wants_milling": wants_milling,
            "demolition_only": demolition_only,
        }

    @staticmethod
    def _blind_plate_install_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_blind_plate = any(term in normalized for term in (
            "\u76f2\u5835\u677f",
            "\u76f2(\u5835)\u677f",
            "\u76f2\uff08\u5835\uff09\u677f",
            "\u76f2\u677f",
            "\u5835\u677f",
        ))
        removal_task = any(term in normalized for term in (
            "\u62c6\u9664",
            "\u62c6\u5378",
            "\u62c6\u4e0b",
            "\u62c6\u6389",
        ))
        dn_match = re.search(r"(?:DN|\u516c\u79f0\u76f4\u5f84(?:MM)?(?:\u4ee5\u5185)?[:\uff1a]?)\s*(\d{2,4})", normalized)
        return {
            "pipe": specialty_text in {"C7", "7"},
            "wants_blind_plate": wants_blind_plate,
            "removal_task": removal_task,
            "dn": dn_match.group(1) if dn_match else "",
        }

    @staticmethod
    def _sidewalk_mortar_bedding_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_sidewalk = any(term in normalized for term in (
            "\u4eba\u884c\u9053\u5757\u6599\u94fa\u8bbe",
            "\u4eba\u884c\u9053\u677f",
            "\u4eba\u884c\u9053",
        ))
        pc_permeable_paver = "PC\u900f\u6c34\u7816" in normalized or ("PC" in normalized and "\u900f\u6c34\u7816" in normalized)
        wants_bedding = "\u57ab\u5c42" in normalized or "\u57fa\u7840\u3001\u57ab\u5c42" in normalized
        wants_mortar = "\u6c34\u6ce5\u7802\u6d46" in normalized or "\u7802\u6d46" in normalized
        pile_subject = any(term in normalized for term in (
            "\u7ba1\u6869",
            "PC\u6869",
            "\u6253\u6869",
            "\u6df7\u51dd\u571f\u7ba1\u6869",
        ))
        return {
            "municipal": specialty_text in {"C2", "2"},
            "wants_sidewalk": wants_sidewalk,
            "pc_permeable_paver": pc_permeable_paver,
            "wants_bedding": wants_bedding,
            "wants_mortar": wants_mortar,
            "pile_subject": pile_subject,
        }

    @staticmethod
    def _hrb400_rebar_install_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_rebar = "\u94a2\u7b4b" in normalized
        wants_deformed = any(term in normalized for term in (
            "HRB400",
            "\u87ba\u7eb9\u94a2",
            "\u5e26\u808b\u94a2\u7b4b",
        ))
        segment_conflict = any(term in normalized for term in (
            "\u7ba1\u7247",
            "\u9884\u5236\u94a2\u7b4b\u6df7\u51dd\u571f\u7ba1\u7247",
            "\u76fe\u6784\u7ba1\u7247",
        ))
        return {
            "road": specialty_text in {"C1", "1"},
            "wants_rebar": wants_rebar,
            "wants_deformed": wants_deformed,
            "segment_conflict": segment_conflict,
        }

    @staticmethod
    def _brick_manhole_shaft_plaster_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_brick_shaft = "\u7816\u780c\u4e95\u7b52" in normalized
        wants_plaster = "\u62b9\u7070" in normalized or "\u7802\u6d46\u62b9" in normalized or "\u62b9\u5149" in normalized
        chimney_conflict = "\u70df\u56f1" in normalized or "\u7b52\u8eab" in normalized
        electrical_conflict = any(term in normalized for term in (
            "\u7535\u7f06\u4e95",
            "\u914d\u7ebf\u624b\u5b54",
            "\u624b\u5b54",
        ))
        return {
            "drainage": specialty_text in {"C6", "6"},
            "wants_brick_shaft": wants_brick_shaft,
            "wants_plaster": wants_plaster,
            "chimney_conflict": chimney_conflict,
            "electrical_conflict": electrical_conflict,
        }

    @staticmethod
    def _collision_barrel_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_barrel = "\u9632\u649e\u7b52" in normalized
        plastic = "\u5851\u6599" in normalized
        rubber = "\u6a61\u80f6" in normalized
        conflict = any(term in normalized for term in (
            "\u6c34\u9a6c",
            "\u62a4\u680f",
            "\u680f\u6746",
            "\u6276\u624b",
            "\u6df7\u51dd\u571f\u9632\u649e\u5899",
        ))
        return {
            "traffic": specialty_text in {"C2", "2"},
            "wants_barrel": wants_barrel,
            "plastic": plastic,
            "rubber": rubber,
            "conflict": conflict,
        }

    @staticmethod
    def _drainage_backfill_material_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_backfill = "\u56de\u586b" in normalized
        wants_yellow_sand = any(term in normalized for term in ("\u9ec4\u7802", "\u7c97\u7802", "\u4e2d\u7c97\u7802"))
        wants_stone_dust = "\u77f3\u5c51" in normalized or "\u77f3\u7c89" in normalized
        wants_crushed_stone = "\u788e\u77f3" in normalized and not wants_stone_dust
        wants_tangkeng = any(term in normalized for term in ("\u5858\u6e23", "\u5858\u78b4", "\u77f3\u6e23", "\u77f3\u78b4"))
        plain_soil = any(term in normalized for term in (
            "\u539f\u571f",
            "\u5f00\u6316\u65b9",
            "\u571f\u65b9\u56de\u586b",
        )) or ("\u5229\u7528\u65b9" in normalized and not wants_tangkeng)
        bedding_context = "\u57ab\u5c42" in normalized
        material_count = sum(bool(flag) for flag in (
            wants_yellow_sand,
            wants_stone_dust,
            wants_crushed_stone,
            wants_tangkeng,
        ))
        return {
            "drainage": specialty_text in {"C6", "6"},
            "wants_backfill": wants_backfill,
            "wants_yellow_sand": wants_yellow_sand,
            "wants_stone_dust": wants_stone_dust,
            "wants_crushed_stone": wants_crushed_stone,
            "wants_tangkeng": wants_tangkeng,
            "plain_soil": plain_soil,
            "bedding_context": bedding_context,
            "material_count": material_count,
        }

    @staticmethod
    def _drainage_channel_concrete_bedding_intent(item: dict, text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        name_normalized = str(item.get("name") or item.get("bill_name") or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_bedding = name_normalized == "\u57ab\u5c42" and "\u57ab\u5c42" in normalized
        concrete = "\u6df7\u51dd\u571f" in normalized or "\u5546\u54c1\u783c" in normalized or bool(re.search(r"\bC\d{2}\b", normalized))
        conflict = any(term in normalized for term in (
            "\u4e95",
            "\u96e8\u6c34\u53e3",
            "\u7ba1\u9053\u57fa\u7840",
            "\u5305\u7ba1",
            "\u5305\u5c01",
            "\u56de\u586b",
            "\u8c03\u5e73\u5c42",
            "\u8def\u706f",
            "\u6c9f\u5e95\u677f",
            "\u58c1\u677f",
        ))
        return {
            "drainage": specialty_text in {"C6", "6"},
            "wants_bedding": wants_bedding,
            "concrete": concrete,
            "conflict": conflict,
        }

    @staticmethod
    def _road_tangkeng_backfill_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_backfill = "\u56de\u586b" in normalized
        wants_tangkeng = "\u5858\u6e23" in normalized or "\u5858\u78b4" in normalized
        road_context = any(term in normalized for term in (
            "\u8def\u57fa",
            "\u516c\u4ea4\u7ad9\u53f0",
            "\u8fc7\u8857",
            "\u94fa\u88c5\u7ed3\u6784",
            "\u7ed3\u6784\u5e95",
            "\u4eba\u884c\u9053",
            "\u9053\u8def",
        ))
        conflict = any(term in normalized for term in (
            "\u539f\u571f",
            "\u5f00\u6316\u65b9",
            "\u571f\u65b9\u56de\u586b",
            "\u6c9f\u69fd",
            "\u7ba1\u9053",
            "\u4e95",
            "\u57ab\u5c42",
        ))
        return {
            "road": specialty_text in {"C2", "2"},
            "wants_backfill": wants_backfill,
            "wants_tangkeng": wants_tangkeng,
            "road_context": road_context,
            "conflict": conflict,
        }

    @staticmethod
    def _crushed_stone_base_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        thickness_marks = re.findall(
            r"(?:\d+(?:\.\d+)?(?:CM|MM)?\u539a|\u539a\u5ea6[:\uff1a]?\d+(?:\.\d+)?(?:CM|MM)?)",
            normalized,
        )
        wants_crushed_stone = "\u788e\u77f3" in normalized
        explicit_base = any(term in normalized for term in ("\u788e\u77f3\u57ab\u5c42", "\u8def\u9762\u7ed3\u6784\u5c42", "\u788e\u77f3("))
        excluded_context = any(term in normalized for term in (
            "\u5730\u4e0b\u5ba4\u9876\u677f",
            "\u9876\u677f\u5904\u7406",
            "\u6e20",
            "\u7ba1\u9053\u57ab\u5c42",
            "\u6e20(\u7ba1)\u9053",
            "\u4e95",
            "\u6ee4\u5c42",
            "\u7802\u783e",
            "\u5757\u77f3",
            "\u6c34\u6ce5\u7a33\u5b9a",
        ))
        return {
            "municipal_road": specialty_text in {"C2", "2"},
            "wants_crushed_stone": wants_crushed_stone,
            "explicit_base": explicit_base,
            "single_thickness": len(set(thickness_marks)) <= 1,
            "thickness_marks": thickness_marks,
            "excluded_context": excluded_context,
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
    def _bored_pile_drilling_method_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_bored_pile = "\u6ce5\u6d46\u62a4\u58c1\u6210\u5b54\u704c\u6ce8\u6869" in normalized or "\u704c\u6ce8\u6869" in normalized
        method = ""
        if any(term in normalized for term in ("\u56de\u65cb\u94bb\u5b54", "\u56de\u65cb\u94bb", "\u56de\u8f6c\u94bb")):
            method = "rotary_circulation"
        elif "\u51b2\u5b54" in normalized:
            method = "percussion"
        elif "\u65cb\u6316" in normalized:
            method = "rotary_excavation"
        return {
            "bridge": specialty_text in {"C3", "3"},
            "wants_bored_pile": wants_bored_pile,
            "method": method,
            "generic_method": "\u7efc\u5408\u8003\u8651" in normalized and not method,
        }

    @staticmethod
    def _bridge_expansion_joint_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_expansion = "\u6865\u6881\u4f38\u7f29\u88c5\u7f6e" in normalized or "\u4f38\u7f29\u7f1d" in normalized
        wants_fiber_concrete = "\u94a2\u7ea4\u7ef4\u6df7\u51dd\u571f" in normalized
        wants_putf = "PUTF" in normalized or "\u805a\u6c28\u916f\u586b\u5145\u5f0f" in normalized
        explicit_steel_shape = "\u578b\u94a2\u4f38\u7f29\u7f1d" in normalized
        explicit_rubber = "\u6a61\u80f6\u677f" in normalized
        return {
            "bridge": specialty_text in {"C3", "3"},
            "wants_expansion": wants_expansion,
            "wants_fiber_concrete": wants_fiber_concrete,
            "wants_putf": wants_putf,
            "explicit_steel_shape": explicit_steel_shape,
            "explicit_rubber": explicit_rubber,
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
    def _modified_bitumen_membrane_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_membrane = "\u9632\u6c34\u5377\u6750" in normalized or ("\u9632\u6c34" in normalized and "\u5377\u6750" in normalized)
        wants_modified_bitumen = (
            "\u6539\u6027\u6ca5\u9752" in normalized
            or "\u6539\u6027\u9752" in normalized
            or "SBS" in normalized
            or "\u5f39\u6027\u4f53\u6539\u6027" in normalized
        )
        wants_self_adhesive = "\u81ea\u7c98" in normalized or "\u81ea\u805a\u7269\u6539\u6027" in normalized or "\u6e7f\u94fa\u6cd5" in normalized
        polymer_only = any(term in normalized for term in ("\u9ad8\u5206\u5b50", "PVC", "TPO", "EPDM")) and not wants_modified_bitumen
        pre_applied = "\u9884\u94fa\u53cd\u7c98" in normalized
        explicit_wall_vertical = "\u5899\u9762" in normalized
        wants_horizontal = any(
            term in normalized
            for term in ("\u5c4b\u9762", "\u697c\uff08\u5730\uff09\u9762", "\u697c\u5730\u9762", "\u9876\u677f", "\u5e73\u9762")
        )
        local_upturn = any(term in normalized for term in ("\u9047\u5899\u4e0a\u7ffb", "\u4e0a\u7ffb", "\u7ffb\u81f3\u7acb\u9762", "\u53cd\u81f3\u7acb\u9762"))
        explicit_vertical = explicit_wall_vertical or ("\u7acb\u9762" in normalized and not local_upturn)
        return {
            "waterproof": specialty_text in {"C9", "9"},
            "wants_membrane": wants_membrane,
            "wants_modified_bitumen": wants_modified_bitumen,
            "wants_self_adhesive": wants_self_adhesive,
            "polymer_only": polymer_only,
            "pre_applied": pre_applied,
            "wants_horizontal": wants_horizontal and not explicit_vertical,
            "wants_vertical": explicit_vertical,
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
    def _polyurethane_waterproof_coating_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        specialty_text = str(specialty or "").strip().upper()
        wants_polyurethane = any(term in normalized for term in ("\u805a\u6c28\u916f", "\u805a\u80fa\u8102", "PU"))
        wants_coating = "\u6d82\u6599" in normalized or "\u6d82\u819c" in normalized
        horizontal_anchor = any(
            term in normalized
            for term in ("\u5c4b\u9762", "\u697c\uff08\u5730\uff09\u9762", "\u697c\u5730\u9762", "\u697c\u9762", "\u5730\u9762", "\u5e73\u9762")
        )
        local_upturn = any(term in normalized for term in ("\u9047\u4fa7\u5899", "\u9047\u5899\u4e0a\u7ffb", "\u4e0a\u7ffb", "\u7ffb\u81f3\u7acb\u9762", "\u53cd\u81f3\u7acb\u9762"))
        vertical_only = any(term in normalized for term in ("\u5899\u9762", "\u7acb\u9762")) and not local_upturn
        increment_only = any(term in normalized for term in ("\u6bcf\u589e", "\u6bcf\u51cf", "\u589e\u52a0\u539a\u5ea6", "\u589e\u539a"))
        other_material = any(term in normalized for term in (
            "\u805a\u5408\u7269\u6c34\u6ce5",
            "JS",
            "\u6c34\u6ce5\u57fa",
            "\u6e17\u900f\u7ed3\u6676",
            "\u6539\u6027\u6ca5\u9752",
            "\u6ca5\u9752",
            "\u5377\u6750",
        ))
        return {
            "waterproof": specialty_text in {"C9", "9"},
            "wants_polyurethane": wants_polyurethane,
            "wants_coating": wants_coating,
            "wants_horizontal": horizontal_anchor and not vertical_only,
            "wants_vertical": vertical_only,
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
    def _large_equipment_demob_intent(text: str, specialty: str = "") -> dict:
        normalized = str(text or "").replace(" ", "").upper()
        wants_large_equipment = "\u5927\u578b\u673a\u68b0\u8bbe\u5907" in normalized or "\u673a\u68b0\u8bbe\u5907" in normalized
        wants_demob = (
            "\u8fdb\u51fa\u573a\u53ca\u5b89\u62c6" in normalized
            or ("\u8fdb\u51fa\u573a" in normalized and "\u5b89\u62c6" in normalized)
        )
        pile_machine = any(
            term in normalized
            for term in (
                "\u53cc\u5934\u6405\u62cc\u6869\u673a",
                "\u6405\u62cc\u6869\u673a",
                "\u6253\u6869\u673a",
                "\u6869\u673a",
                "\u94bb\u5b54\u673a",
            )
        )
        return {
            "wants_large_equipment": wants_large_equipment,
            "wants_demob": wants_demob,
            "pile_machine": pile_machine,
            "mentions_trd": "TRD" in normalized,
            "specialty": str(specialty or "").strip().upper(),
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
    def _apply_crushed_stone_base_rescue(
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
        intent = cls._crushed_stone_base_intent(item_text, str(specialty))
        if (
            not intent["municipal_road"]
            or not intent["wants_crushed_stone"]
            or not intent["explicit_base"]
            or not intent["single_thickness"]
            or intent["excluded_context"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:10], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            conflict = any(term in normalized for term in (
                "\u7802\u783e\u77f3",
                "\u5757\u77f3",
                "\u6ee4\u5c42",
                "\u704c\u6d46",
                "\u4e95",
                "\u6e20",
                "\u7ba1)\u9053",
                "\u57fa\u7840\u57ab\u5c42",
                "\u6bcf\u51cf",
                "\u6c34\u6ce5\u7a33\u5b9a",
            ))
            score = 0
            if candidate_prefix == "2":
                score += 10
            else:
                score -= 20
            if "\u788e\u77f3\u5e95\u5c42" in normalized:
                score += 10
            if "\u4eba\u673a\u914d\u5408" in normalized:
                score += 6
            if "\u539a20CM" in normalized:
                score += 4
            if "\u4eba\u5de5\u94fa\u88c5" in normalized:
                score -= 5
            if "\u788e\u77f3\u57ab\u5c42" in normalized and "\u788e\u77f3\u5e95\u5c42" not in normalized:
                score -= 4
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
            if score < 26:
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
        reason = "crushed_stone_base_confirmed" if candidate is ltr_ranked[0] else "crushed_stone_base_rescued"
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
    def _apply_traffic_sign_shape_rescue(
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
        intent = cls._traffic_sign_shape_intent(item_text, str(specialty))
        if (
            not intent["municipal"]
            or not intent["wants_sign"]
            or not intent["wants_triangle"]
            or not intent["triangle_size"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            candidate_intent = cls._traffic_sign_shape_intent(candidate_text, str(specialty))
            conflict = any(term in normalized for term in (
                "\u9762\u79ef",
                "\u957f\u65b9\u5f62",
                "\u6b63\u65b9\u5f62",
                "\u5706\u5f62",
                "\u6807\u5fd7\u6746",
                "\u7bad\u5934",
                "\u677f\u94dd\u6a21",
            ))
            score = 0
            if candidate_prefix == "2":
                score += 8
            else:
                score -= 20
            if "\u6807\u5fd7\u724c" in normalized:
                score += 6
            if candidate_intent["wants_triangle"]:
                score += 8
            if candidate_intent["triangle_size"] and candidate_intent["triangle_size"] == intent["triangle_size"]:
                score += 10
            elif candidate_intent["triangle_size"]:
                score -= 6
            if conflict:
                score -= 10

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "candidate_intent": candidate_intent,
                "conflict": conflict,
                "score": score,
            })
            if score < 26:
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
            "traffic_sign_shape_confirmed"
            if candidate is ltr_ranked[0]
            else "traffic_sign_shape_rescued"
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
    def _apply_geotextile_tape_rescue(
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
        intent = cls._geotextile_tape_intent(item_text, str(specialty))
        if (
            not intent["municipal"]
            or not intent["wants_geosynthetic"]
            or not intent["wants_tape"]
            or intent["explicit_laying"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            conflict = any(term in normalized for term in (
                "\u5e73\u94fa",
                "\u659c\u94fa",
                "\u571f\u5de5\u683c\u6805",
                "\u6392\u6c34\u7f51",
                "\u57ab\u5c42",
                "\u5730\u819c",
            ))
            score = 0
            if candidate_prefix == "2":
                score += 8
            else:
                score -= 20
            if "\u571f\u5de5\u5e03\u8d34\u7f1d" in normalized:
                score += 18
            elif "\u8d34\u7f1d" in normalized:
                score += 12
            if "\u571f\u5de5\u5408\u6210\u6750\u6599" in normalized or "\u571f\u5de5\u5e03" in normalized:
                score += 3
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
            if score < 24:
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
            "geotextile_tape_confirmed"
            if candidate is ltr_ranked[0]
            else "geotextile_tape_rescued"
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
    def _apply_road_saw_cut_joint_rescue(
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
        intent = cls._road_saw_cut_joint_intent(item_text, str(specialty))
        if (
            not intent["municipal"]
            or not intent["wants_road"]
            or not intent["wants_cut"]
            or intent["deformation_joint"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:20], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            conflict = any(term in normalized for term in (
                "\u571f\u5de5\u5e03",
                "\u8d34\u7f1d",
                "\u4f38\u7f1d",
                "\u53d8\u5f62\u7f1d",
                "\u739b\u8e44\u8102",
                "\u586b\u704c\u7f1d",
                "\u9632\u6ed1\u6761",
                "\u51ff\u6bdb",
                "\u52fe\u7f1d",
            ))
            score = 0
            if candidate_prefix == "2":
                score += 10
            else:
                score -= 20
            if "\u952f\u7f1d\u673a\u5207\u7f1d" in normalized:
                score += 14
            elif "\u5207\u7f1d" in normalized or "\u952f\u7f1d" in normalized:
                score += 8
            if "\u7f1d\u6df1(CM)5" in normalized or "\u7f1d\u6df15" in normalized:
                score += 6
            if "\u6bcf\u589e\u51cf" in normalized or "\u6bcf\u589e" in normalized or "\u6bcf\u51cf" in normalized:
                score -= 8
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
            if score < 28:
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
        reason = "road_saw_cut_joint_confirmed" if candidate is ltr_ranked[0] else "road_saw_cut_joint_rescued"
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
    def _apply_shotcrete_slope_base_rescue(
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
        intent = cls._shotcrete_slope_base_intent(item_text, str(specialty))
        if (
            not intent["municipal"]
            or not intent["wants_shotcrete"]
            or not intent["wants_slope"]
            or not intent["slope_within_60"]
            or intent["tunnel_or_spray_conflict"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:20], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            increment = "\u6bcf\u589e\u51cf" in normalized or "\u6bcf\u589e" in normalized or "\u6bcf\u51cf" in normalized
            wrong_slope = any(term in normalized for term in ("\u5761\u5ea6>60", "\u5761\u5ea6\uff1e60", "\u5761\u5ea6<15", "\u5761\u5ea6\uff1c15"))
            conflict = any(term in normalized for term in (
                "\u6d1e\u5185",
                "\u62f1\u90e8",
                "\u7ba1\u9053",
                "\u5185\u55b7\u6d82",
                "\u6c34\u6ce5\u7802\u6d46",
                "\u62b9\u7070",
                "\u6302\u7f51",
                "\u6405\u62cc\u6869",
            ))
            score = 0
            if candidate_prefix == "2":
                score += 8
            else:
                score -= 20
            if "\u55b7\u5c04\u6df7\u51dd\u571f\u62a4\u5761" in normalized:
                score += 14
            elif "\u55b7\u5c04\u6df7\u51dd\u571f" in normalized and "\u62a4\u5761" in normalized:
                score += 10
            if "\u5761\u5ea6<60" in normalized or "\u5761\u5ea6\uff1c60" in normalized or "\u5761\u5ea660" in normalized:
                score += 10
            if "\u539a\u5ea650MM" in normalized or "\u539a\u5ea650" in normalized:
                score += 6
            if increment:
                score -= 14
            if wrong_slope:
                score -= 12
            if conflict:
                score -= 12

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "increment": increment,
                "wrong_slope": wrong_slope,
                "conflict": conflict,
                "score": score,
            })
            if score < 30:
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
        reason = "shotcrete_slope_base_confirmed" if candidate is ltr_ranked[0] else "shotcrete_slope_base_rescued"
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
    def _apply_road_milling_base_rescue(
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
        intent = cls._road_milling_base_intent(item_text, str(specialty))
        if not intent["demolition"] or not intent["wants_milling"] or intent["demolition_only"]:
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:20], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            increment = "\u6bcf\u589e\u51cf" in normalized or "\u6bcf\u589e" in normalized or "\u6bcf\u51cf" in normalized
            demolition_conflict = any(term in normalized for term in (
                "\u62c6\u9664",
                "\u98ce\u9550",
                "\u4eba\u5de5\u62c6",
                "\u5ca9\u77f3\u7834\u788e\u673a",
                "\u51ff\u6bdb",
                "\u57fa\u5c42\u6216\u9762\u5c42",
            ))
            score = 0
            if candidate_prefix == "1":
                score += 8
            else:
                score -= 20
            if "\u94e3\u5228\u673a\u94e3\u5228\u8def\u9762" in normalized:
                score += 22
            elif "\u94e3\u5228" in normalized and "\u8def\u9762" in normalized:
                score += 16
            if "\u539a\u5ea63CM" in normalized or "\u539a\u5ea63" in normalized:
                score += 5
            if increment:
                score -= 12
            if demolition_conflict:
                score -= 12

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "increment": increment,
                "demolition_conflict": demolition_conflict,
                "score": score,
            })
            if score < 30:
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
        reason = "road_milling_base_confirmed" if candidate is ltr_ranked[0] else "road_milling_base_rescued"
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
    def _apply_blind_plate_install_rescue(
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
        intent = cls._blind_plate_install_intent(item_text, str(specialty))
        if not intent["pipe"] or not intent["wants_blind_plate"] or intent.get("removal_task"):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:20], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            candidate_dn_match = re.search(r"(?:DN|\u516c\u79f0\u76f4\u5f84(?:\(MM\u4ee5\u5185\))?(?:MM)?(?:\u4ee5\u5185)?[:\uff1a]?)\s*(\d{2,4})", normalized)
            candidate_dn = candidate_dn_match.group(1) if candidate_dn_match else ""
            blind_plate_candidate = any(term in normalized for term in (
                "\u76f2\u5835\u677f",
                "\u76f2(\u5835)\u677f",
                "\u76f2\uff08\u5835\uff09\u677f",
                "\u76f2\u677f",
                "\u5835\u677f",
            ))
            conflict = any(term in normalized for term in (
                "\u951a\u6746",
                "\u951a\u7d22",
                "\u571f\u9489",
                "\u94a2\u7b4b",
                "\u94a2\u7ba1",
                "\u692d\u693d\u677f",
                "\u6728\u5de5\u677f",
            ))
            score = 0
            if candidate_prefix == "7":
                score += 8
            else:
                score -= 20
            if blind_plate_candidate and "\u5b89\u88c5" in normalized:
                score += 22
            elif blind_plate_candidate:
                score += 18
            if intent["dn"]:
                if candidate_dn == intent["dn"]:
                    score += 10
                elif candidate_dn:
                    score -= 10
            if conflict:
                score -= 14

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "candidate_dn": candidate_dn,
                "blind_plate_candidate": blind_plate_candidate,
                "conflict": conflict,
                "score": score,
            })
            if score < 32:
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
        reason = "blind_plate_install_confirmed" if candidate is ltr_ranked[0] else "blind_plate_install_rescued"
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
    def _apply_sidewalk_mortar_bedding_rescue(
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
        intent = cls._sidewalk_mortar_bedding_intent(item_text, str(specialty))
        if (
            not intent["municipal"]
            or not intent["wants_sidewalk"]
            or not intent["pc_permeable_paver"]
            or not intent["wants_bedding"]
            or not intent["wants_mortar"]
            or intent["pile_subject"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:20], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            sidewalk_mortar = "\u4eba\u884c\u9053\u677f\u7802\u6d46\u57ab\u5c42" in normalized
            sidewalk_bedding = "\u4eba\u884c\u9053" in normalized and "\u57ab\u5c42" in normalized
            mortar = "\u6c34\u6ce5\u7802\u6d46" in normalized or "\u7802\u6d46" in normalized
            increment = "\u6bcf\u589e\u51cf" in normalized or "\u6bcf\u589e" in normalized or "\u6bcf\u51cf" in normalized
            conflict = any(term in normalized for term in (
                "\u7ba1\u6869",
                "PC\u6869",
                "\u6253\u6869",
                "\u5f00\u6316",
                "\u4fee\u590d",
                "\u6df7\u51dd\u571f\u57fa\u7840",
                "\u62b9\u9762",
            ))
            score = 0
            if candidate_prefix == "2":
                score += 8
            else:
                score -= 20
            if sidewalk_mortar:
                score += 26
            elif sidewalk_bedding and mortar:
                score += 20
            elif sidewalk_bedding:
                score += 12
            if "\u539a\u5ea62CM" in normalized or "\u539a\u5ea62" in normalized:
                score += 4
            if increment:
                score -= 12
            if conflict:
                score -= 16

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "sidewalk_mortar": sidewalk_mortar,
                "sidewalk_bedding": sidewalk_bedding,
                "mortar": mortar,
                "increment": increment,
                "conflict": conflict,
                "score": score,
            })
            if score < 34:
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
        reason = "sidewalk_mortar_bedding_confirmed" if candidate is ltr_ranked[0] else "sidewalk_mortar_bedding_rescued"
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
    def _apply_hrb400_rebar_install_rescue(
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
        intent = cls._hrb400_rebar_install_intent(item_text, str(specialty))
        if not intent["road"] or not intent["wants_rebar"] or not intent["wants_deformed"] or intent["segment_conflict"]:
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:20], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            ordinary_deformed = "\u666e\u901a\u94a2\u7b4b\u5236\u4f5c\u3001\u5b89\u88c5" in normalized and "\u5e26\u808b\u94a2\u7b4b" in normalized
            deformed = "\u5e26\u808b\u94a2\u7b4b" in normalized
            conflict = any(term in normalized for term in (
                "\u7ba1\u7247",
                "\u690d\u7b4b",
                "\u780c\u4f53\u5185\u52a0\u56fa",
                "\u94a2\u7b4b\u7f51",
                "\u6302\u8d34\u94a2\u7b4b\u7f51",
                "\u9884\u5236\u6784\u4ef6\u5b89\u88c5",
                "\u5706\u94a2",
                "\u51b7\u8f67",
            ))
            score = 0
            if candidate_prefix == "1":
                score += 8
            else:
                score -= 18
            if ordinary_deformed:
                score += 26
            elif deformed:
                score += 14
            if conflict:
                score -= 14

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "ordinary_deformed": ordinary_deformed,
                "deformed": deformed,
                "conflict": conflict,
                "score": score,
            })
            if score < 30:
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
        reason = "hrb400_rebar_install_confirmed" if candidate is ltr_ranked[0] else "hrb400_rebar_install_rescued"
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
    def _apply_brick_manhole_shaft_plaster_rescue(
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
        intent = cls._brick_manhole_shaft_plaster_intent(item_text, str(specialty))
        if (
            not intent["drainage"]
            or not intent["wants_brick_shaft"]
            or not intent["wants_plaster"]
            or intent["chimney_conflict"]
            or intent["electrical_conflict"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:20], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            manhole_plaster_wall = "\u62b9\u7070\u4e95\u58c1" in normalized
            manhole_plaster_bottom = "\u62b9\u7070\u4e95\u5e95" in normalized
            brick_manhole = "\u4e95\u780c\u7b51" in normalized and "\u7816\u780c" in normalized
            manhole_wall = "\u4e95\u58c1" in normalized
            chimney = "\u70df\u56f1" in normalized or "\u7b52\u8eab" in normalized
            electrical = any(term in normalized for term in (
                "\u7535\u7f06\u4e95",
                "\u914d\u7ebf\u624b\u5b54",
                "\u624b\u5b54",
                "\u6df1\u4e95\u9633\u6781",
            ))
            depth_increment = "\u6bcf\u589e\u51cf" in normalized or "\u6bcf\u589e" in normalized or "\u6bcf\u51cf" in normalized

            score = 0
            if candidate_prefix == "6":
                score += 12
            else:
                score -= 20
            if manhole_plaster_wall:
                score += 30
            elif brick_manhole and manhole_wall:
                score += 26
            elif brick_manhole:
                score += 24
            elif manhole_plaster_bottom:
                score += 8
            if manhole_plaster_bottom and not manhole_wall:
                score -= 12
            if chimney:
                score -= 30
            if electrical:
                score -= 18
            if depth_increment:
                score -= 10

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "manhole_plaster_wall": manhole_plaster_wall,
                "manhole_plaster_bottom": manhole_plaster_bottom,
                "brick_manhole": brick_manhole,
                "manhole_wall": manhole_wall,
                "chimney": chimney,
                "electrical": electrical,
                "depth_increment": depth_increment,
                "score": score,
            })
            if score < 34:
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
            "brick_manhole_shaft_plaster_confirmed"
            if candidate is ltr_ranked[0]
            else "brick_manhole_shaft_plaster_rescued"
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
    def _apply_collision_barrel_rescue(
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
        intent = cls._collision_barrel_intent(item_text, str(specialty))
        if not intent["traffic"] or not intent["wants_barrel"] or intent["conflict"]:
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:12], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            barrel = "\u9632\u649e\u7b52" in normalized
            collision_facility = "\u9632\u649e\u9694\u79bb\u8bbe\u65bd" in normalized
            plastic = "\u5851\u6599\u9632\u649e\u7b52" in normalized
            rubber = "\u6a61\u80f6\u9632\u649e\u7b52" in normalized
            water_horse = "\u6c34\u9a6c" in normalized
            guardrail = "\u62a4\u680f" in normalized or "\u6276\u624b" in normalized
            stone_drum = "\u9f13\u78f4" in normalized or "\u5706\u5f62\u9f13" in normalized
            round_timber = "\u5706\u6728" in normalized or "\u5706\u67f1" in normalized

            score = 0
            if candidate_prefix == "2":
                score += 8
            else:
                score -= 18
            if collision_facility:
                score += 8
            if barrel:
                score += 22
            if intent["plastic"]:
                score += 8 if plastic else -4
            elif intent["rubber"]:
                score += 8 if rubber else -4
            else:
                score += 4 if rubber else 0
            if water_horse:
                score -= 16
            if guardrail:
                score -= 14
            if stone_drum or round_timber:
                score -= 18

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "barrel": barrel,
                "collision_facility": collision_facility,
                "plastic": plastic,
                "rubber": rubber,
                "water_horse": water_horse,
                "guardrail": guardrail,
                "stone_drum": stone_drum,
                "round_timber": round_timber,
                "score": score,
            })
            if score < 34:
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
        reason = "collision_barrel_confirmed" if candidate is ltr_ranked[0] else "collision_barrel_rescued"
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
    def _apply_drainage_backfill_material_rescue(
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
        intent = cls._drainage_backfill_material_intent(item_text, str(specialty))
        if (
            not intent["drainage"]
            or not intent["wants_backfill"]
            or intent["material_count"] != 1
            or intent["plain_soil"]
            or intent["bedding_context"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        target_material = ""
        if intent["wants_yellow_sand"]:
            target_material = "\u9ec4\u7802"
        elif intent["wants_stone_dust"]:
            target_material = "\u77f3\u5c51"
        elif intent["wants_crushed_stone"]:
            target_material = "\u788e\u77f3"
        elif intent["wants_tangkeng"]:
            target_material = "\u5858\u78b4"

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:20], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            drainage_backfill = "\u6c9f\u69fd\u56de\u586b" in normalized
            yellow_sand = "\u9ec4\u7802" in normalized
            stone_dust = "\u77f3\u5c51" in normalized or "\u77f3\u7c89" in normalized
            crushed_stone = "\u788e\u77f3" in normalized and not stone_dust
            tangkeng = "\u5858\u78b4" in normalized or "\u5858\u6e23" in normalized
            soil_fill = any(term in normalized for term in (
                "\u56de\u586b\u571f",
                "\u586b\u571f",
                "\u79cd\u690d\u571f",
                "\u539f\u571f",
            ))
            other_material = any((
                target_material != "\u9ec4\u7802" and yellow_sand,
                target_material != "\u77f3\u5c51" and stone_dust,
                target_material != "\u788e\u77f3" and crushed_stone,
                target_material != "\u5858\u78b4" and tangkeng,
            ))
            material_match = any((
                target_material == "\u9ec4\u7802" and yellow_sand,
                target_material == "\u77f3\u5c51" and stone_dust,
                target_material == "\u788e\u77f3" and crushed_stone,
                target_material == "\u5858\u78b4" and tangkeng,
            ))

            score = 0
            if candidate_prefix == "6":
                score += 8
            else:
                score -= 18
            if drainage_backfill:
                score += 14
            if material_match:
                score += 20
            if other_material:
                score -= 10
            if soil_fill:
                score -= 14
            if "\u4eba\u5de5\u7ea7\u914d" in normalized and target_material != "\u788e\u77f3":
                score -= 6

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "drainage_backfill": drainage_backfill,
                "target_material": target_material,
                "material_match": material_match,
                "other_material": other_material,
                "soil_fill": soil_fill,
                "score": score,
            })
            if score < 36:
                continue
            if best is None or score > best[0] or (score == best[0] and rank < best[1]):
                best = (score, rank, candidate)

        if best is None:
            return False, "", {
                "item_text": item_text,
                "specialty": str(specialty),
                "intent": intent,
                "target_material": target_material,
                "inspected": inspected,
            }, ltr_ranked

        _score, rank, candidate = best
        ranked = [candidate] + [entry for entry in ltr_ranked if entry is not candidate]
        reason = (
            "drainage_backfill_material_confirmed"
            if candidate is ltr_ranked[0]
            else "drainage_backfill_material_rescued"
        )
        return True, reason, {
            "item_text": item_text,
            "specialty": str(specialty),
            "intent": intent,
            "target_material": target_material,
            "rescued_rank": rank,
            "rescued_quota_id": str(candidate.get("quota_id") or ""),
            "rescued_text": cls._candidate_query_text(candidate),
            "inspected": inspected,
        }, ranked

    @classmethod
    def _apply_drainage_channel_concrete_bedding_rescue(
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
        intent = cls._drainage_channel_concrete_bedding_intent(item, item_text, str(specialty))
        if (
            not intent["drainage"]
            or not intent["wants_bedding"]
            or not intent["concrete"]
            or intent["conflict"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:20], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            channel_bedding = "\u6e20(\u7ba1)\u9053\u57ab\u5c42" in normalized
            concrete = "\u6df7\u51dd\u571f" in normalized
            conflict = any(term in normalized for term in (
                "\u4e95",
                "\u56de\u586b",
                "\u6c9f\u69fd",
                "\u6c9f\u5e95\u677f",
                "\u58c1\u677f",
                "\u5e95\u677f",
                "\u788e\u77f3",
                "\u5858\u78b4",
                "\u7802",
            ))

            score = 0
            if candidate_prefix == "6":
                score += 8
            else:
                score -= 18
            if channel_bedding:
                score += 22
            if concrete:
                score += 14
            if conflict:
                score -= 16

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "channel_bedding": channel_bedding,
                "concrete": concrete,
                "conflict": conflict,
                "score": score,
            })
            if score < 40:
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
            "drainage_channel_concrete_bedding_confirmed"
            if candidate is ltr_ranked[0]
            else "drainage_channel_concrete_bedding_rescued"
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
    def _apply_road_tangkeng_backfill_rescue(
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
        intent = cls._road_tangkeng_backfill_intent(item_text, str(specialty))
        if (
            not intent["road"]
            or not intent["wants_backfill"]
            or not intent["wants_tangkeng"]
            or not intent["road_context"]
            or intent["conflict"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:20], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            road_fill = "\u8def\u57fa\u586b\u7b51" in normalized
            tangkeng = "\u5858\u6e23" in normalized or "\u5858\u78b4" in normalized
            conflict = any(term in normalized for term in (
                "\u6c9f\u69fd\u56de\u586b",
                "\u56de\u586b\u571f",
                "\u586b\u571f",
                "\u79cd\u690d\u571f",
                "\u77f3\u78b4\u56de\u586b",
                "\u539f\u571f",
                "\u57ab\u5c42",
            ))

            score = 0
            if candidate_prefix == "2":
                score += 8
            else:
                score -= 18
            if road_fill:
                score += 20
            if tangkeng:
                score += 18
            if conflict:
                score -= 14

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "road_fill": road_fill,
                "tangkeng": tangkeng,
                "conflict": conflict,
                "score": score,
            })
            if score < 40:
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
        reason = "road_tangkeng_backfill_confirmed" if candidate is ltr_ranked[0] else "road_tangkeng_backfill_rescued"
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
    def _apply_bored_pile_drilling_method_rescue(
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
        intent = cls._bored_pile_drilling_method_intent(item_text, str(specialty))
        if not intent["bridge"] or not intent["wants_bored_pile"] or not intent["method"]:
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        positive_terms = {
            "rotary_circulation": ("\u56de\u65cb\u94bb\u5b54", "\u56de\u65cb\u94bb", "\u56de\u8f6c\u94bb"),
            "percussion": ("\u51b2\u5b54",),
            "rotary_excavation": ("\u65cb\u6316",),
        }
        conflict_terms = {
            "rotary_circulation": ("\u51b2\u5b54", "\u65cb\u6316", "\u4eba\u5de5\u6316\u5b54"),
            "percussion": ("\u56de\u65cb\u94bb\u5b54", "\u56de\u65cb\u94bb", "\u56de\u8f6c\u94bb", "\u65cb\u6316", "\u4eba\u5de5\u6316\u5b54"),
            "rotary_excavation": ("\u56de\u65cb\u94bb\u5b54", "\u56de\u65cb\u94bb", "\u56de\u8f6c\u94bb", "\u51b2\u5b54", "\u4eba\u5de5\u6316\u5b54"),
        }

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        method = str(intent["method"])
        for rank, candidate in enumerate(ltr_ranked[:20], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            matches_method = any(term in normalized for term in positive_terms[method])
            conflicts_method = any(term in normalized for term in conflict_terms[method])
            non_pile_conflict = any(term in normalized for term in (
                "\u94a2\u7b4b\u7b3c",
                "\u51ff\u9664",
                "\u62a4\u58c1",
                "\u5730\u811a\u87ba\u6813",
                "\u704c\u6d46\u5b54",
            ))

            score = 0
            if candidate_prefix == "3":
                score += 8
            else:
                score -= 20
            if "\u704c\u6ce8\u6869" in normalized:
                score += 3
            if "\u6210\u5b54" in normalized:
                score += 3
            if matches_method:
                score += 20
            if conflicts_method:
                score -= 14
            if non_pile_conflict:
                score -= 10

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "matches_method": matches_method,
                "conflicts_method": conflicts_method,
                "non_pile_conflict": non_pile_conflict,
                "score": score,
            })
            if score < 28:
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
            "bored_pile_drilling_method_confirmed"
            if candidate is ltr_ranked[0]
            else "bored_pile_drilling_method_rescued"
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
    def _apply_bridge_expansion_joint_rescue(
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
        intent = cls._bridge_expansion_joint_intent(item_text, str(specialty))
        if (
            not intent["bridge"]
            or not intent["wants_expansion"]
            or not (intent["wants_fiber_concrete"] or intent["wants_putf"])
            or intent["explicit_steel_shape"]
            or intent["explicit_rubber"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            score = 0
            if candidate_prefix == "3":
                score += 6
            if "\u4f38\u7f29\u7f1d\u94a2\u7ea4\u7ef4\u6df7\u51dd\u571f" in normalized:
                score += 22
            elif "\u94a2\u7ea4\u7ef4\u6df7\u51dd\u571f" in normalized and "\u4f38\u7f29\u7f1d" in normalized:
                score += 18
            if "\u805a\u6c28\u916f" in normalized:
                score += 6
            if "\u578b\u94a2\u4f38\u7f29\u7f1d" in normalized:
                score -= 14
            if "\u6bdb\u52d2" in normalized or "\u6a61\u80f6\u677f" in normalized or "\u6ca5\u9752\u9ebb\u4e1d" in normalized:
                score -= 10
            if "\u76ae\u5e26\u673a" in normalized:
                score -= 18

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "score": score,
            })
            if score < 20:
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
            "bridge_expansion_joint_confirmed"
            if candidate is ltr_ranked[0]
            else "bridge_expansion_joint_rescued"
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
    def _apply_modified_bitumen_membrane_rescue(
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
        intent = cls._modified_bitumen_membrane_intent(item_text, str(specialty))
        if (
            not intent["waterproof"]
            or not intent["wants_membrane"]
            or not intent["wants_modified_bitumen"]
            or intent["polymer_only"]
            or intent["pre_applied"]
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:10], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            score = 0
            if candidate_prefix == "9":
                score += 4
            else:
                score -= 16
            if "\u6539\u6027\u6ca5\u9752\u81ea\u7c98\u5377\u6750" in normalized:
                score += 20
            elif "\u6539\u6027\u6ca5\u9752" in normalized and "\u5377\u6750" in normalized:
                score += 14
            if "\u81ea\u7c98\u6cd5" in normalized:
                score += 6
            if "\u4e00\u5c42" in normalized:
                score += 3
            if "\u6bcf\u589e" in normalized:
                score -= 8
            if "\u5e73\u9762" in normalized:
                score += 5 if intent["wants_horizontal"] else -2
            if "\u7acb\u9762" in normalized:
                score += 5 if intent["wants_vertical"] else -2
            if "\u9ad8\u5206\u5b50\u5377\u6750" in normalized or "\u9ad8\u5206\u5b50" in normalized:
                score -= 18
            if "\u94a0\u57fa\u81a8\u6da6\u571f" in normalized or "\u9884\u94fa\u53cd\u7c98" in normalized:
                score -= 12

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "prefix": candidate_prefix,
                "score": score,
            })
            if score < 24:
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
            "modified_bitumen_membrane_confirmed"
            if candidate is ltr_ranked[0]
            else "modified_bitumen_membrane_rescued"
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
    def _apply_polyurethane_waterproof_coating_rescue(
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
        intent = cls._polyurethane_waterproof_coating_intent(item_text, str(specialty))
        if (
            not intent["waterproof"]
            or not intent["wants_polyurethane"]
            or not intent["wants_coating"]
            or intent["increment_only"]
            or intent["other_material"]
            or not (intent["wants_horizontal"] or intent["wants_vertical"])
        ):
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:12], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            candidate_prefix = cls._quota_major_prefix(candidate.get("quota_id"))
            conflict = any(term in normalized for term in (
                "\u6bcf\u589e",
                "\u6bcf\u51cf",
                "\u6539\u6027\u6ca5\u9752",
                "\u6ca5\u9752",
                "\u805a\u5408\u7269\u6c34\u6ce5",
                "JS",
                "\u6c34\u6ce5\u57fa",
                "\u6e17\u900f\u7ed3\u6676",
                "\u5377\u6750",
            ))
            score = 0
            if candidate_prefix == "9":
                score += 10
            else:
                score -= 20
            if "\u805a\u6c28\u916f\u9632\u6c34\u6d82\u6599" in normalized or "\u805a\u80fa\u8102\u9632\u6c34\u6d82\u6599" in normalized:
                score += 10
            elif ("\u805a\u6c28\u916f" in normalized or "\u805a\u80fa\u8102" in normalized) and "\u6d82\u6599" in normalized:
                score += 8
            if "\u539a\u5ea61.5MM" in normalized or "\u539a\u5ea61.5" in normalized:
                score += 5
            if intent["wants_horizontal"]:
                if "\u5e73\u9762" in normalized:
                    score += 6
                if "\u7acb\u9762" in normalized:
                    score -= 8
            if intent["wants_vertical"]:
                if "\u7acb\u9762" in normalized:
                    score += 6
                if "\u5e73\u9762" in normalized:
                    score -= 8
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
            if score < 24:
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
            "polyurethane_waterproof_coating_confirmed"
            if candidate is ltr_ranked[0]
            else "polyurethane_waterproof_coating_rescued"
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
    def _apply_large_equipment_demob_rescue(
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
        intent = cls._large_equipment_demob_intent(item_text, str(specialty))
        if not intent["wants_large_equipment"] or not intent["wants_demob"] or not intent["pile_machine"]:
            return False, "", {"item_text": item_text, "specialty": str(specialty), "intent": intent}, ltr_ranked

        inspected: list[dict] = []
        best: tuple[int, int, dict] | None = None
        for rank, candidate in enumerate(ltr_ranked[:8], start=1):
            candidate_text = cls._candidate_query_text(candidate)
            normalized = candidate_text.replace(" ", "").upper()
            score = 0
            if "\u5b89\u62c6\u8d39\u7528" in normalized:
                score += 20
            if "\u6869\u673a" in normalized or "\u6253\u6869\u673a" in normalized:
                score += 8
            if "\u67f4\u6cb9\u6253\u6869\u673a" in normalized:
                score += 5
            if "TRD" in normalized:
                score += 8 if intent["mentions_trd"] else -10
            if "\u573a\u5916\u8fd0\u8f93\u8d39\u7528" in normalized:
                score -= 12
            if "\u573a\u5730\u673a\u68b0\u5e73\u6574" in normalized or "\u5e73\u6574" in normalized:
                score -= 30
            if "\u5f3a\u592f" in normalized and "\u5f3a\u592f" not in item_text:
                score -= 8

            inspected.append({
                "rank": rank,
                "quota_id": str(candidate.get("quota_id") or ""),
                "text": candidate_text,
                "score": score,
            })
            if score < 24:
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
            "large_equipment_demob_confirmed"
            if candidate is ltr_ranked[0]
            else "large_equipment_demob_rescued"
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

        pile_method_blocked, pile_method_reason, pile_method_details, pile_method_ranked = cls._apply_bored_pile_drilling_method_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["bored_pile_drilling_method_rescue"] = {
            "blocked": pile_method_blocked,
            "reason": pile_method_reason,
            "details": pile_method_details,
        }
        if pile_method_blocked:
            rescued_id = str(pile_method_ranked[0].get("quota_id", "") or "") if pile_method_ranked else ""
            pile_method_ranked[0]["_rank_score_source"] = "manual"
            pile_method_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = pile_method_reason
            meta["final_top1_id"] = rescued_id
            return pile_method_ranked, meta

        bridge_joint_blocked, bridge_joint_reason, bridge_joint_details, bridge_joint_ranked = cls._apply_bridge_expansion_joint_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["bridge_expansion_joint_rescue"] = {
            "blocked": bridge_joint_blocked,
            "reason": bridge_joint_reason,
            "details": bridge_joint_details,
        }
        if bridge_joint_blocked:
            rescued_id = str(bridge_joint_ranked[0].get("quota_id", "") or "") if bridge_joint_ranked else ""
            bridge_joint_ranked[0]["_rank_score_source"] = "manual"
            bridge_joint_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = bridge_joint_reason
            meta["final_top1_id"] = rescued_id
            return bridge_joint_ranked, meta

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

        modified_bitumen_blocked, modified_bitumen_reason, modified_bitumen_details, modified_bitumen_ranked = cls._apply_modified_bitumen_membrane_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["modified_bitumen_membrane_rescue"] = {
            "blocked": modified_bitumen_blocked,
            "reason": modified_bitumen_reason,
            "details": modified_bitumen_details,
        }
        if modified_bitumen_blocked:
            rescued_id = str(modified_bitumen_ranked[0].get("quota_id", "") or "") if modified_bitumen_ranked else ""
            modified_bitumen_ranked[0]["_rank_score_source"] = "manual"
            modified_bitumen_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = modified_bitumen_reason
            meta["final_top1_id"] = rescued_id
            return modified_bitumen_ranked, meta

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

        polyurethane_blocked, polyurethane_reason, polyurethane_details, polyurethane_ranked = cls._apply_polyurethane_waterproof_coating_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["polyurethane_waterproof_coating_rescue"] = {
            "blocked": polyurethane_blocked,
            "reason": polyurethane_reason,
            "details": polyurethane_details,
        }
        if polyurethane_blocked:
            rescued_id = str(polyurethane_ranked[0].get("quota_id", "") or "") if polyurethane_ranked else ""
            polyurethane_ranked[0]["_rank_score_source"] = "manual"
            polyurethane_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = polyurethane_reason
            meta["final_top1_id"] = rescued_id
            return polyurethane_ranked, meta

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

        equipment_blocked, equipment_reason, equipment_details, equipment_ranked = cls._apply_large_equipment_demob_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["large_equipment_demob_rescue"] = {
            "blocked": equipment_blocked,
            "reason": equipment_reason,
            "details": equipment_details,
        }
        if equipment_blocked:
            rescued_id = str(equipment_ranked[0].get("quota_id", "") or "") if equipment_ranked else ""
            equipment_ranked[0]["_rank_score_source"] = "manual"
            equipment_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = equipment_reason
            meta["final_top1_id"] = rescued_id
            return equipment_ranked, meta

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

        sign_shape_blocked, sign_shape_reason, sign_shape_details, sign_shape_ranked = cls._apply_traffic_sign_shape_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["traffic_sign_shape_rescue"] = {
            "blocked": sign_shape_blocked,
            "reason": sign_shape_reason,
            "details": sign_shape_details,
        }
        if sign_shape_blocked:
            rescued_id = str(sign_shape_ranked[0].get("quota_id", "") or "") if sign_shape_ranked else ""
            sign_shape_ranked[0]["_rank_score_source"] = "manual"
            sign_shape_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = sign_shape_reason
            meta["final_top1_id"] = rescued_id
            return sign_shape_ranked, meta

        geotextile_blocked, geotextile_reason, geotextile_details, geotextile_ranked = cls._apply_geotextile_tape_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["geotextile_tape_rescue"] = {
            "blocked": geotextile_blocked,
            "reason": geotextile_reason,
            "details": geotextile_details,
        }
        if geotextile_blocked:
            rescued_id = str(geotextile_ranked[0].get("quota_id", "") or "") if geotextile_ranked else ""
            geotextile_ranked[0]["_rank_score_source"] = "manual"
            geotextile_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = geotextile_reason
            meta["final_top1_id"] = rescued_id
            return geotextile_ranked, meta

        saw_cut_blocked, saw_cut_reason, saw_cut_details, saw_cut_ranked = cls._apply_road_saw_cut_joint_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["road_saw_cut_joint_rescue"] = {
            "blocked": saw_cut_blocked,
            "reason": saw_cut_reason,
            "details": saw_cut_details,
        }
        if saw_cut_blocked:
            rescued_id = str(saw_cut_ranked[0].get("quota_id", "") or "") if saw_cut_ranked else ""
            saw_cut_ranked[0]["_rank_score_source"] = "manual"
            saw_cut_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = saw_cut_reason
            meta["final_top1_id"] = rescued_id
            return saw_cut_ranked, meta

        shotcrete_blocked, shotcrete_reason, shotcrete_details, shotcrete_ranked = cls._apply_shotcrete_slope_base_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["shotcrete_slope_base_rescue"] = {
            "blocked": shotcrete_blocked,
            "reason": shotcrete_reason,
            "details": shotcrete_details,
        }
        if shotcrete_blocked:
            rescued_id = str(shotcrete_ranked[0].get("quota_id", "") or "") if shotcrete_ranked else ""
            shotcrete_ranked[0]["_rank_score_source"] = "manual"
            shotcrete_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = shotcrete_reason
            meta["final_top1_id"] = rescued_id
            return shotcrete_ranked, meta

        milling_blocked, milling_reason, milling_details, milling_ranked = cls._apply_road_milling_base_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["road_milling_base_rescue"] = {
            "blocked": milling_blocked,
            "reason": milling_reason,
            "details": milling_details,
        }
        if milling_blocked:
            rescued_id = str(milling_ranked[0].get("quota_id", "") or "") if milling_ranked else ""
            milling_ranked[0]["_rank_score_source"] = "manual"
            milling_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = milling_reason
            meta["final_top1_id"] = rescued_id
            return milling_ranked, meta

        blind_plate_blocked, blind_plate_reason, blind_plate_details, blind_plate_ranked = cls._apply_blind_plate_install_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["blind_plate_install_rescue"] = {
            "blocked": blind_plate_blocked,
            "reason": blind_plate_reason,
            "details": blind_plate_details,
        }
        if blind_plate_blocked:
            rescued_id = str(blind_plate_ranked[0].get("quota_id", "") or "") if blind_plate_ranked else ""
            blind_plate_ranked[0]["_rank_score_source"] = "manual"
            blind_plate_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = blind_plate_reason
            meta["final_top1_id"] = rescued_id
            return blind_plate_ranked, meta

        sidewalk_bedding_blocked, sidewalk_bedding_reason, sidewalk_bedding_details, sidewalk_bedding_ranked = cls._apply_sidewalk_mortar_bedding_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["sidewalk_mortar_bedding_rescue"] = {
            "blocked": sidewalk_bedding_blocked,
            "reason": sidewalk_bedding_reason,
            "details": sidewalk_bedding_details,
        }
        if sidewalk_bedding_blocked:
            rescued_id = str(sidewalk_bedding_ranked[0].get("quota_id", "") or "") if sidewalk_bedding_ranked else ""
            sidewalk_bedding_ranked[0]["_rank_score_source"] = "manual"
            sidewalk_bedding_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = sidewalk_bedding_reason
            meta["final_top1_id"] = rescued_id
            return sidewalk_bedding_ranked, meta

        hrb400_rebar_blocked, hrb400_rebar_reason, hrb400_rebar_details, hrb400_rebar_ranked = cls._apply_hrb400_rebar_install_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["hrb400_rebar_install_rescue"] = {
            "blocked": hrb400_rebar_blocked,
            "reason": hrb400_rebar_reason,
            "details": hrb400_rebar_details,
        }
        if hrb400_rebar_blocked:
            rescued_id = str(hrb400_rebar_ranked[0].get("quota_id", "") or "") if hrb400_rebar_ranked else ""
            hrb400_rebar_ranked[0]["_rank_score_source"] = "manual"
            hrb400_rebar_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = hrb400_rebar_reason
            meta["final_top1_id"] = rescued_id
            return hrb400_rebar_ranked, meta

        brick_manhole_blocked, brick_manhole_reason, brick_manhole_details, brick_manhole_ranked = cls._apply_brick_manhole_shaft_plaster_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["brick_manhole_shaft_plaster_rescue"] = {
            "blocked": brick_manhole_blocked,
            "reason": brick_manhole_reason,
            "details": brick_manhole_details,
        }
        if brick_manhole_blocked:
            rescued_id = str(brick_manhole_ranked[0].get("quota_id", "") or "") if brick_manhole_ranked else ""
            brick_manhole_ranked[0]["_rank_score_source"] = "manual"
            brick_manhole_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = brick_manhole_reason
            meta["final_top1_id"] = rescued_id
            return brick_manhole_ranked, meta

        collision_barrel_blocked, collision_barrel_reason, collision_barrel_details, collision_barrel_ranked = cls._apply_collision_barrel_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["collision_barrel_rescue"] = {
            "blocked": collision_barrel_blocked,
            "reason": collision_barrel_reason,
            "details": collision_barrel_details,
        }
        if collision_barrel_blocked:
            rescued_id = str(collision_barrel_ranked[0].get("quota_id", "") or "") if collision_barrel_ranked else ""
            collision_barrel_ranked[0]["_rank_score_source"] = "manual"
            collision_barrel_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = collision_barrel_reason
            meta["final_top1_id"] = rescued_id
            return collision_barrel_ranked, meta

        drainage_backfill_blocked, drainage_backfill_reason, drainage_backfill_details, drainage_backfill_ranked = cls._apply_drainage_backfill_material_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["drainage_backfill_material_rescue"] = {
            "blocked": drainage_backfill_blocked,
            "reason": drainage_backfill_reason,
            "details": drainage_backfill_details,
        }
        if drainage_backfill_blocked:
            rescued_id = str(drainage_backfill_ranked[0].get("quota_id", "") or "") if drainage_backfill_ranked else ""
            drainage_backfill_ranked[0]["_rank_score_source"] = "manual"
            drainage_backfill_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = drainage_backfill_reason
            meta["final_top1_id"] = rescued_id
            return drainage_backfill_ranked, meta

        drainage_bedding_blocked, drainage_bedding_reason, drainage_bedding_details, drainage_bedding_ranked = cls._apply_drainage_channel_concrete_bedding_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["drainage_channel_concrete_bedding_rescue"] = {
            "blocked": drainage_bedding_blocked,
            "reason": drainage_bedding_reason,
            "details": drainage_bedding_details,
        }
        if drainage_bedding_blocked:
            rescued_id = str(drainage_bedding_ranked[0].get("quota_id", "") or "") if drainage_bedding_ranked else ""
            drainage_bedding_ranked[0]["_rank_score_source"] = "manual"
            drainage_bedding_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = drainage_bedding_reason
            meta["final_top1_id"] = rescued_id
            return drainage_bedding_ranked, meta

        road_tangkeng_blocked, road_tangkeng_reason, road_tangkeng_details, road_tangkeng_ranked = cls._apply_road_tangkeng_backfill_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["road_tangkeng_backfill_rescue"] = {
            "blocked": road_tangkeng_blocked,
            "reason": road_tangkeng_reason,
            "details": road_tangkeng_details,
        }
        if road_tangkeng_blocked:
            rescued_id = str(road_tangkeng_ranked[0].get("quota_id", "") or "") if road_tangkeng_ranked else ""
            road_tangkeng_ranked[0]["_rank_score_source"] = "manual"
            road_tangkeng_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = road_tangkeng_reason
            meta["final_top1_id"] = rescued_id
            return road_tangkeng_ranked, meta

        crushed_stone_blocked, crushed_stone_reason, crushed_stone_details, crushed_stone_ranked = cls._apply_crushed_stone_base_rescue(
            item,
            ltr_ranked,
            context,
        )
        meta["crushed_stone_base_rescue"] = {
            "blocked": crushed_stone_blocked,
            "reason": crushed_stone_reason,
            "details": crushed_stone_details,
        }
        if crushed_stone_blocked:
            rescued_id = str(crushed_stone_ranked[0].get("quota_id", "") or "") if crushed_stone_ranked else ""
            crushed_stone_ranked[0]["_rank_score_source"] = "manual"
            crushed_stone_ranked[0]["ltr_guard_blocked"] = True
            meta["action"] = "blocked"
            meta["reason"] = crushed_stone_reason
            meta["final_top1_id"] = rescued_id
            return crushed_stone_ranked, meta

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
