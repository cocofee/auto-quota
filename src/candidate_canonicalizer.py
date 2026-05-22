from __future__ import annotations

from typing import Any

from src.candidate_feature_store import get_candidate_feature_store
from src.specialty_classifier import get_book_from_quota_id
from src.text_parser import parser as text_parser


_PARAM_KEYS = (
    "dn", "conduit_dn", "cable_section", "cable_cores", "cable_bundle",
    "kva", "kw", "kv", "ampere", "circuits", "port_count", "weight_t",
    "perimeter", "half_perimeter", "large_side", "ground_bar_width",
    "elevator_stops", "elevator_speed", "switch_gangs", "shape",
    "elevator_type", "cable_type", "cable_head_type", "conduit_type",
    "wire_type", "box_mount_mode", "bridge_type", "valve_connection_family",
    "support_scope", "support_action", "sanitary_mount_mode",
    "sanitary_flush_mode", "sanitary_water_mode", "sanitary_nozzle_mode",
    "sanitary_tank_mode", "lamp_type", "outlet_grounding",
    "material", "connection", "install_method", "laying_method",
)

_MATERIAL_HYDRATION_HINTS = (
    "薄钢板", "镀锌钢板", "白铁", "白铁皮", "铝皮", "铁皮",
    "碳钢", "不锈钢", "钢板", "铜芯", "铝芯",
)
_CONNECTION_HYDRATION_HINTS = (
    "法兰", "无法兰", "铆钉", "焊接", "沟槽", "卡箍",
    "螺纹", "丝扣", "热熔", "电熔", "承插",
)
_VALVE_TYPE_HYDRATION_HINTS = (
    "防火阀", "排烟防火阀", "防火调节阀", "调节阀",
    "多叶调节阀", "止回阀", "蝶阀", "排气阀",
)
_AIR_ENTITY_HYDRATION_HINTS = ("风管", "风口", "风阀", "散流器", "百叶风口")
_FAMILY_HYDRATION_HINTS = ("风管", "风口", "风阀", "风机", "排气阀")


def _candidate_text(candidate: dict[str, Any]) -> str:
    return " ".join(
        str(part or "").strip()
        for part in (
            candidate.get("name", ""),
            candidate.get("description", ""),
        )
        if str(part or "").strip()
    ).strip()


def _candidate_hydration_text(candidate: dict[str, Any], raw_text: str) -> str:
    return " ".join(
        str(part or "").strip()
        for part in (
            raw_text,
            candidate.get("material", ""),
            candidate.get("connection", ""),
            candidate.get("valve_type", ""),
        )
        if str(part or "").strip()
    )


def _missing_feature(features: dict[str, Any], key: str) -> bool:
    return features.get(key) in (None, "", [])


def _cached_features_need_hydration(cached: dict[str, Any], candidate: dict[str, Any], raw_text: str) -> bool:
    """旧缓存可能是空壳；只在文本明确含结构线索但缓存缺字段时重建。"""
    hint_text = _candidate_hydration_text(candidate, raw_text)
    if not hint_text:
        return False

    if _missing_feature(cached, "material") and any(token in hint_text for token in _MATERIAL_HYDRATION_HINTS):
        return True
    if _missing_feature(cached, "connection") and any(token in hint_text for token in _CONNECTION_HYDRATION_HINTS):
        return True
    if _missing_feature(cached, "valve_type") and any(token in hint_text for token in _VALVE_TYPE_HYDRATION_HINTS):
        return True
    if _missing_feature(cached, "entity") and any(token in hint_text for token in _AIR_ENTITY_HYDRATION_HINTS):
        return True
    if _missing_feature(cached, "family") and any(token in hint_text for token in _FAMILY_HYDRATION_HINTS):
        return True
    return False


def build_candidate_params(candidate: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key in _PARAM_KEYS:
        value = candidate.get(key)
        if value not in (None, "", []):
            params[key] = value
    return params


def build_candidate_canonical_features(candidate: dict[str, Any],
                                       specialty: str = "",
                                       province: str = "") -> dict[str, Any]:
    raw_text = _candidate_text(candidate)
    candidate_specialty = (
        candidate.get("specialty")
        or specialty
        or get_book_from_quota_id(candidate.get("quota_id", ""))
        or ""
    )
    cached = candidate.get("candidate_canonical_features") or candidate.get("canonical_features")
    if cached and not _cached_features_need_hydration(dict(cached), candidate, raw_text):
        return dict(cached)

    store = get_candidate_feature_store()
    store_cached = store.get(province, candidate)
    if store_cached and not _cached_features_need_hydration(dict(store_cached), candidate, raw_text):
        candidate["candidate_canonical_features"] = dict(store_cached)
        return store_cached

    params = text_parser.parse(raw_text)
    params.update(build_candidate_params(candidate))
    features = text_parser.parse_canonical(
        raw_text or candidate.get("name", ""),
        specialty=candidate_specialty,
        params=params,
    )
    if features:
        candidate["candidate_canonical_features"] = dict(features)
    store.put(province, candidate, features)
    return features


def build_candidate_canonical_features_no_store(candidate: dict[str, Any],
                                                specialty: str = "") -> dict[str, Any]:
    """Build canonical features without touching candidate_features.db."""
    raw_text = _candidate_text(candidate) or str(candidate.get("name", "") or "").strip()
    candidate_specialty = (
        candidate.get("specialty")
        or specialty
        or get_book_from_quota_id(candidate.get("quota_id", ""))
        or ""
    )
    params = text_parser.parse(raw_text)
    params.update(build_candidate_params(candidate))
    features = text_parser.parse_canonical(
        raw_text,
        specialty=candidate_specialty,
        params=params,
    )
    if features:
        candidate["candidate_canonical_features"] = dict(features)
    return features


def attach_candidate_canonical_features(candidates: list[dict[str, Any]],
                                        specialty: str = "",
                                        province: str = "") -> list[dict[str, Any]]:
    for candidate in candidates or []:
        features = build_candidate_canonical_features(
            candidate,
            specialty=specialty,
            province=province,
        )
        if features:
            candidate["canonical_features"] = dict(features)
    return candidates
