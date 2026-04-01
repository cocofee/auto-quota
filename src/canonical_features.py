from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.canonical_dictionary import (
    build_numeric_params,
    build_specs,
    collect_traits,
    detect_entity,
    detect_family,
    detect_system,
    normalize_connection,
    normalize_install_method,
    normalize_material,
    normalize_text,
    resolve_canonical_name,
)


def _coalesce_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _normalize_laying_method(value: object, raw_text: str, context_prior: dict[str, Any]) -> str:
    explicit = str(value or "").strip()
    merged = " ".join(
        part for part in (
            explicit,
            raw_text,
            " ".join(str(item or "") for item in (context_prior.get("context_hints") or [])),
        ) if part
    )
    has_bridge = any(token in merged for token in ("桥架", "沿桥架", "桥内"))
    has_trunking = any(token in merged for token in ("线槽", "槽内"))
    has_conduit = any(token in merged for token in ("穿管", "管内", "配管"))
    has_exposed = "明配" in merged
    has_hidden = "暗配" in merged

    if has_bridge and has_conduit:
        return "桥架/穿管"
    if has_bridge:
        return "桥架"
    if has_trunking:
        return "线槽"
    if has_conduit:
        return "穿管"
    if has_exposed:
        return "明配"
    if has_hidden:
        return "暗配"
    return explicit


def _normalize_box_mount_mode(value: object, install_method: str) -> str:
    text = str(value or "").strip()
    if not text:
        if install_method == "落地":
            return "落地式"
        return ""
    if any(token in text for token in ("落地", "柜基础", "基础槽钢")):
        return "落地式"
    if any(token in text for token in ("悬挂", "壁挂", "挂墙", "挂式", "嵌入", "嵌墙")):
        return "悬挂/嵌入式"
    return text


def _normalize_support_action(value: object, raw_text: str = "") -> str:
    explicit = str(value or "").strip()
    merged = " ".join(part for part in (explicit, raw_text) if part)
    if not merged:
        return ""
    has_make = "制作" in merged
    has_install = "安装" in merged
    if has_make and has_install:
        return "制作安装"
    if has_make:
        return "制作"
    if has_install:
        return "安装"
    return explicit


def _normalize_support_scope(value: object, raw_text: str, entity: str) -> str:
    explicit = str(value or "").strip()
    if explicit:
        return explicit
    if entity not in {"支吊架", "桥架"}:
        return ""
    if "桥架支撑架" in raw_text:
        return "桥架支撑架"
    if any(token in raw_text for token in ("桥架", "电缆桥架", "母线槽", "线槽")) and any(
        token in raw_text for token in ("支撑架", "支架", "支吊架")
    ):
        return "桥架支撑架"
    if "管道支架" in raw_text or "一般管架" in raw_text:
        return "管道支架"
    if "管道" in raw_text and any(token in raw_text for token in ("支架", "支吊架", "管架")):
        return "管道支架"
    return ""


def _normalize_entity(value: str, raw_text: str) -> str:
    entity = str(value or "").strip()
    if entity == "支吊架" and any(token in raw_text for token in ("桥架", "电缆桥架", "线槽", "母线槽")):
        return "桥架"
    return entity


def _refine_entity_with_context(entity: str,
                                *,
                                raw_text: str,
                                laying_method: str,
                                support_scope: str,
                                system: str,
                                context_prior: dict[str, Any] | None = None) -> str:
    entity = str(entity or "").strip()
    if entity != "支吊架":
        return entity

    context_prior = dict(context_prior or {})
    context_hints = " ".join(str(value or "") for value in (context_prior.get("context_hints") or []))
    combined = " ".join(
        part for part in (
            raw_text,
            laying_method,
            support_scope,
            system,
            context_hints,
            str(context_prior.get("prior_family") or ""),
        ) if part
    )
    if any(token in combined for token in ("桥架", "电缆桥架", "线槽", "母线槽")):
        return "桥架"
    return entity


def _merge_traits(base_traits: list[str], *extra_values: object) -> list[str]:
    traits = [str(value).strip() for value in base_traits if str(value).strip()]
    for value in extra_values:
        text = str(value or "").strip()
        if text:
            traits.append(text)
    return list(dict.fromkeys(traits))


@dataclass
class CanonicalFeatureSet:
    raw_text: str
    normalized_text: str
    canonical_name: str = ""
    entity: str = ""
    family: str = ""
    specialty: str = ""
    system: str = ""
    material: str = ""
    cable_type: str = ""
    cable_head_type: str = ""
    conduit_type: str = ""
    wire_type: str = ""
    box_mount_mode: str = ""
    bridge_type: str = ""
    valve_connection_family: str = ""
    support_scope: str = ""
    support_action: str = ""
    sanitary_mount_mode: str = ""
    sanitary_flush_mode: str = ""
    sanitary_water_mode: str = ""
    sanitary_nozzle_mode: str = ""
    sanitary_tank_mode: str = ""
    lamp_type: str = ""
    outlet_grounding: str = ""
    connection: str = ""
    install_method: str = ""
    laying_method: str = ""
    voltage_level: str = ""
    valve_type: str = ""
    support_material: str = ""
    surface_process: str = ""
    sanitary_subtype: str = ""
    numeric_params: dict[str, Any] = field(default_factory=dict)
    specs: dict[str, Any] = field(default_factory=dict)
    dn: int | None = None
    cable_section: float | None = None
    bridge_wh_sum: float | None = None
    cable_bundle: list[dict[str, Any]] = field(default_factory=list)
    kva: float | None = None
    kw: float | None = None
    ampere: float | None = None
    circuits: int | None = None
    port_count: int | None = None
    traits: list[str] = field(default_factory=list)
    context_prior: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_canonical_features(raw_text: str,
                             params: dict[str, Any] | None = None,
                             specialty: str = "",
                             context_prior: dict[str, Any] | None = None) -> CanonicalFeatureSet:
    params = params or {}
    context_prior = dict(context_prior or {})

    normalized_text = normalize_text(raw_text)
    material = normalize_material(str(params.get("material") or ""), normalized_text)
    connection = normalize_connection(str(params.get("connection") or ""), normalized_text)
    install_method = normalize_install_method(str(params.get("install_method") or ""), normalized_text)
    laying_method = _normalize_laying_method(
        params.get("laying_method"),
        normalized_text,
        context_prior,
    )
    box_mount_mode = _normalize_box_mount_mode(params.get("box_mount_mode"), install_method)
    entity = _normalize_entity(detect_entity(normalized_text), normalized_text)
    support_action = _normalize_support_action(params.get("support_action"), normalized_text)
    support_scope = _normalize_support_scope(params.get("support_scope"), normalized_text, entity)
    system = detect_system(
        normalized_text,
        specialty=specialty,
        context_prior=context_prior,
        entity=entity,
    )
    entity = _refine_entity_with_context(
        entity,
        raw_text=normalized_text,
        laying_method=laying_method,
        support_scope=support_scope,
        system=system,
        context_prior=context_prior,
    )
    traits = _merge_traits(
        collect_traits(
            {
                **params,
                "laying_method": laying_method,
                "box_mount_mode": box_mount_mode,
                "support_scope": support_scope,
                "support_action": support_action,
            },
            context_prior=context_prior,
            raw_text=normalized_text,
        ),
        install_method,
        connection,
    )
    family = detect_family(
        normalized_text,
        entity=entity,
        system=system,
        material=material,
        install_method=install_method,
        traits=traits,
        context_prior=context_prior,
    )
    canonical_name = resolve_canonical_name(
        normalized_text,
        entity=entity,
        material=material,
    )

    return CanonicalFeatureSet(
        raw_text=raw_text or "",
        normalized_text=normalized_text,
        canonical_name=canonical_name,
        entity=entity,
        family=family,
        specialty=specialty or "",
        system=system,
        material=material,
        cable_type=str(params.get("cable_type") or ""),
        cable_head_type=str(params.get("cable_head_type") or ""),
        conduit_type=str(params.get("conduit_type") or ""),
        wire_type=str(params.get("wire_type") or ""),
        box_mount_mode=box_mount_mode,
        bridge_type=str(params.get("bridge_type") or ""),
        valve_connection_family=str(params.get("valve_connection_family") or ""),
        support_scope=support_scope,
        support_action=support_action,
        sanitary_mount_mode=str(params.get("sanitary_mount_mode") or ""),
        sanitary_flush_mode=str(params.get("sanitary_flush_mode") or ""),
        sanitary_water_mode=str(params.get("sanitary_water_mode") or ""),
        sanitary_nozzle_mode=str(params.get("sanitary_nozzle_mode") or ""),
        sanitary_tank_mode=str(params.get("sanitary_tank_mode") or ""),
        lamp_type=str(params.get("lamp_type") or ""),
        outlet_grounding=str(params.get("outlet_grounding") or ""),
        connection=connection,
        install_method=install_method,
        laying_method=laying_method,
        voltage_level=str(params.get("voltage_level") or ""),
        valve_type=str(params.get("valve_type") or ""),
        support_material=str(params.get("support_material") or ""),
        surface_process=str(params.get("surface_process") or ""),
        sanitary_subtype=str(params.get("sanitary_subtype") or ""),
        numeric_params=build_numeric_params(params),
        specs=build_specs(
            {
                **params,
                "install_method": install_method,
                "laying_method": laying_method,
                "box_mount_mode": box_mount_mode,
                "support_scope": support_scope,
                "support_action": support_action,
                "connection": connection,
            }
        ),
        dn=params.get("dn"),
        cable_section=params.get("cable_section"),
        bridge_wh_sum=params.get("bridge_wh_sum"),
        cable_bundle=list(params.get("cable_bundle") or []),
        kva=params.get("kva"),
        kw=params.get("kw"),
        ampere=params.get("ampere"),
        circuits=params.get("circuits"),
        port_count=params.get("port_count"),
        traits=traits,
        context_prior=context_prior,
    )
