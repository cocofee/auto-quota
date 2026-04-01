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
    cached = candidate.get("candidate_canonical_features") or candidate.get("canonical_features")
    if cached:
        return dict(cached)

    store = get_candidate_feature_store()
    cached = store.get(province, candidate)
    if cached:
        return cached

    raw_text = " ".join(
        part for part in (candidate.get("name", ""), candidate.get("description", ""))
        if part
    ).strip()
    params = text_parser.parse(raw_text)
    params.update(build_candidate_params(candidate))
    candidate_specialty = (
        candidate.get("specialty")
        or specialty
        or get_book_from_quota_id(candidate.get("quota_id", ""))
        or ""
    )
    features = text_parser.parse_canonical(
        raw_text or candidate.get("name", ""),
        specialty=candidate_specialty,
        params=params,
    )
    store.put(province, candidate, features)
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
        candidate.setdefault("canonical_features", dict(features))
    return candidates
